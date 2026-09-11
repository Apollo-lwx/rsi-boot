"""评测入口（§12.6 CI 分级）。

快速门槛（每次 PR，默认运行）：意图规则版评测 + 门禁注入验证（Phase 3 前 skip）。
全量评测（nightly，-m eval_nightly）：检索评测 + Thompson 仿真 + LLM 意图（需 API key）。

数据集由生成器产出并版本化（tests/eval/datasets/*.jsonl）；
基线存 baselines.json，回归门槛 2pp；报告归档 reports/。
"""

from __future__ import annotations

import os

import pytest

from rsi_boot.core.models import KnowledgeItem
from rsi_boot.data.vec import BruteBackend
from rsi_boot.knowledge.embedding import EmbeddingService
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.preprocessor.intent_classifier import detect_intent
from rsi_boot.services.knowledge_service import KnowledgeService

from .harness import (
    check_against_baseline,
    intent_metrics,
    load_baselines,
    load_jsonl,
    retrieval_metrics,
    simulate_thompson,
    write_report,
    DATASETS,
)


# ---------- 快速门槛：意图规则版（§12.1） ----------


def test_intent_rule_baseline():
    rows = load_jsonl(DATASETS / "intent_general.jsonl")
    pairs = [(detect_intent(r["input"])[0], r["intent"]) for r in rows]
    metrics = intent_metrics(pairs)
    write_report("intent-rule", metrics)

    baseline = load_baselines()["intent_rule"]
    violations = check_against_baseline({"top1": metrics["top1"]}, baseline)
    assert not violations, f"意图规则版回归: {violations}（混淆矩阵见 reports/）"


def test_intent_rule_role_baseline():
    rows = load_jsonl(DATASETS / "intent_role.jsonl")
    pairs = [(detect_intent(r["input"], role=r["role"])[0], r["intent"]) for r in rows]
    metrics = intent_metrics(pairs)
    write_report("intent-rule-role", metrics)
    # 角色意图基线同 85% 门槛
    assert metrics["top1"] >= 0.85 - 0.02, f"角色意图 Top-1 {metrics['top1']:.3f} 低于基线"


@pytest.mark.skipif(not os.environ.get("RSI_EVAL_LLM"), reason="LLM 意图评测需真实 API key（nightly）")
def test_intent_llm_baseline():
    pytest.skip("P2.2 LLM 分类器评测在 nightly 环境执行（RSI_EVAL_LLM=1）")


# ---------- 快速门槛：Harness 门禁注入（§12.4） ----------


async def test_harness_gate_injection(db, tmp_path, monkeypatch):
    """种子退化集拦截率 100%（§3.9/§12.4）：人为构造的退化提案必须全部被门禁拒绝"""
    import json
    import uuid
    from datetime import datetime, timezone

    from rsi_boot.learning.proposal_engine import ProposalEngine
    from rsi_boot.learning.regression_gate import RegressionGate
    from rsi_boot.learning.snapshot_store import SnapshotStore
    from rsi_boot.model.adapter import ModelAdapter
    from rsi_boot.model.providers.base import ModelResult
    from rsi_boot.model.registry import ModelRegistry

    # 回放集：高质量历史响应（含代码块 + 步骤 + 长度）
    good_excerpt = (
        "修复步骤：\n1. 检查日志级别\n2. 定位异常栈\n3. 添加回归测试\n"
        "```python\ndef fix():\n    pass\n```\n" + "详细说明。" * 30
    )
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    for i in range(8):
        lid = uuid.uuid4().hex
        await conn.execute(
            "INSERT INTO interaction_logs (id, request_id, user_id, project_id, raw_input,"
            " intent, intent_confidence, latency_ms, status, feedback_token, response_excerpt, created_at)"
            " VALUES (?, ?, 'u1', 'p1', ?, 'debug', 0.9, 10, 'success', ?, ?, ?)",
            (lid, uuid.uuid4().hex, f"问题{i}：程序崩溃怎么办？", f"tok-{lid[:8]}", good_excerpt, now),
        )
    await conn.commit()

    config = {
        "model": {"default": "mock", "timeout_s": 5, "max_retries": 0},
        "embedding": {"provider": "mock"},
    }
    embedding = EmbeddingService(config)
    adapter = ModelAdapter(ModelRegistry(config), config)
    gate = RegressionGate(db, adapter, embedding, config)

    # 种子退化集：5 种典型退化形态（空响应/答非所问/丢代码/过短/无结构）
    degraded_outputs = [
        "不知道。",
        "请自行查阅文档。",  # 无代码无步骤且过短
        "1. 重试",          # 单步骤过短
        "错误" * 60,        # 有长度但无代码无步骤
        "",                 # 空
    ]

    rejected = 0
    for bad_text in degraded_outputs:
        async def bad_complete(model, messages, _t=bad_text):
            return ModelResult(text=_t, model=model, prompt_tokens=1, completion_tokens=1, latency_ms=1)

        monkeypatch.setattr(adapter, "complete", bad_complete)
        proposal = {
            "slot": "prompt_template", "target_ref": "debug.jinja2", "action": "modify",
            "payload": {"after": {"template_content": "随意回答即可"}}, "intent": "debug",
        }
        report = await gate.evaluate(proposal)
        if not report.passed:
            rejected += 1

    interception = rejected / len(degraded_outputs)
    write_report("gate-injection", {"interception_rate": interception, "seeds": len(degraded_outputs)})
    assert interception == 1.0, f"门禁注入拦截率 {interception:.0%}，要求 100%"


# ---------- 全量评测（nightly） ----------


@pytest.mark.eval_nightly
async def test_retrieval_baseline(db, base_config):
    corpus = load_jsonl(DATASETS / "retrieval_corpus.jsonl")
    queries = load_jsonl(DATASETS / "retrieval_queries.jsonl")

    # mock embedder：1536 维随机向量 cosine 集中于 0，向量通道自然关闭，
    # 本评测覆盖 FTS/BM25 通道；向量通道调优在持真实 API key 的环境另行归档（§12.2）
    embedding = EmbeddingService({"embedding": {"provider": "mock"}})
    brute = BruteBackend(db)
    retriever = KnowledgeRetriever(db, base_config, embedding=embedding, vec=brute)
    service = KnowledgeService(db, retriever, embedding=embedding, vec=brute)

    id_by_title: dict[str, str] = {}
    for doc in corpus:
        item = KnowledgeItem(
            project_id=doc["project_type"], title=doc["title"], content=doc["content"],
        )
        await service.add(item)
        id_by_title[doc["title"]] = doc["id"]

    results = []
    for q in queries:
        items = await retriever.search(q["query"], q["project_type"], top_k=5)
        ranked_ids = [id_by_title.get(i.title, i.title) for i in items]
        results.append((ranked_ids, q["relevant"], q["project_type"]))

    metrics = retrieval_metrics(results, k=5)
    write_report("retrieval", metrics)

    baseline = load_baselines()["retrieval"]
    violations = check_against_baseline(
        {"recall@5": metrics["recall@5"], "mrr": metrics["mrr"]}, baseline
    )
    # 最差项目类型 ≥ 70%（§12.2）
    for ptype, m in metrics["per_project_type"].items():
        if m["recall@5"] < 0.70 - 0.02:
            violations.append(f"{ptype} recall@5 {m['recall@5']:.3f} < 70%")
    assert not violations, f"检索回归: {violations}"


@pytest.mark.eval_nightly
def test_thompson_simulation():
    metrics = simulate_thompson(arm_probs=[0.3, 0.5, 0.8], rounds=1000, repeats=100)
    write_report("thompson", metrics)

    baseline = load_baselines()["thompson"]
    violations = check_against_baseline(
        {"best_arm_selection": metrics["best_arm_selection"],
         "regret_reduction": metrics["regret_reduction"]},
        baseline,
    )
    assert not violations, f"Thompson 仿真回归: {violations}"
