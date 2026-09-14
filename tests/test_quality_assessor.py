"""P3.5 质量评估测试（§3.5）：三维度计算 / 角色权重 / 抽样与差评必查 / 管线集成"""

import pytest

from rsi_boot.core.models import RSIRequest
from rsi_boot.feedback.implicit_tracker import FeedbackWorker, ImplicitEvent
from rsi_boot.knowledge.embedding import EmbeddingService
from rsi_boot.model.adapter import ModelAdapter
from rsi_boot.model.providers.base import ModelResult
from rsi_boot.model.registry import ModelRegistry
from rsi_boot.memory.logstore import iter_events
from rsi_boot.orchestrator.pipeline import Pipeline
from rsi_boot.quality.assessor import QualityAssessor, _rescale_cosine, actionability_score
from rsi_boot.knowledge.retriever import KnowledgeRetriever


def _config(**overrides):
    cfg = {
        "model": {"default": "mock", "timeout_s": 5, "max_retries": 0},
        "retrieval": {"top_n": 5, "token_budget": 2000},
        "embedding": {"provider": "mock"},
        "quality": {"accuracy_sample_rate": 0.0, "judge_model": "mock-judge"},
    }
    cfg.update(overrides)
    return cfg


def _assessor(config):
    embedding = EmbeddingService(config)
    adapter = ModelAdapter(ModelRegistry(config), config)
    return QualityAssessor(embedding, adapter, config), adapter


# ---------- actionability 规则 ----------


def test_actionability_full_score():
    text = "步骤如下：\n1. 安装依赖\n2. 配置环境\n3. 启动服务\n```python\nprint('ok')\n```\n" + "补充" * 60
    assert actionability_score(text, "code_gen") == 1.0


def test_actionability_empty_text():
    assert actionability_score("", "code_gen") == 0.0


def test_actionability_partial():
    # 仅长度达标（≥100 字符，无代码块无步骤列表）
    assert actionability_score("字" * 120, "code_gen") == pytest.approx(0.2)
    # 仅步骤列表（≥3 项）
    assert actionability_score("- a\n- b\n- c", "explain") == pytest.approx(0.3)


def test_actionability_intent_thresholds():
    text = "字" * 150  # code_gen 100 达标；explain 200 不达标
    assert actionability_score(text, "code_gen") == pytest.approx(0.2)
    assert actionability_score(text, "explain") == 0.0


# ---------- relevance 重标定 ----------


def test_rescale_cosine():
    assert _rescale_cosine(0.1) == 0.0
    assert _rescale_cosine(0.9) == 1.0
    assert _rescale_cosine(0.5) == pytest.approx(0.5)


# ---------- 综合分与角色权重 ----------


async def test_combine_role_weights():
    assessor, _ = _assessor(_config())
    # developer：0.3/0.3/0.4
    score = assessor._combine("developer", relevance=1.0, accuracy=1.0, actionability=0.0)
    assert score == pytest.approx(0.6)
    # pm：0.5/0.3/0.2
    score = assessor._combine("pm", relevance=1.0, accuracy=1.0, actionability=0.0)
    assert score == pytest.approx(0.8)


async def test_combine_renormalize_without_accuracy():
    assessor, _ = _assessor(_config())
    # default 权重 0.4/0.35/0.25；accuracy 缺席 → 按 0.4:0.25 归一化（结果保留 4 位小数）
    score = assessor._combine(None, relevance=1.0, accuracy=None, actionability=0.0)
    assert score == pytest.approx(round(0.4 / 0.65, 4))


async def test_assess_no_sampling_stashes_context():
    config = _config()
    assessor, _ = _assessor(config)
    result = await assessor.assess("查询", "回答" * 100, "explain", None, feedback_token="tok1")
    assert result.accuracy is None
    assert result.accuracy_sampled is False
    assert "tok1" in assessor._pending_judge  # 差评必查上下文已暂存
    assert 0.0 <= result.quality_score <= 1.0


async def test_judge_accuracy_parses_json(monkeypatch):
    config = _config()
    assessor, adapter = _assessor(config)

    async def fake_complete(model, messages):
        return ModelResult(text='{"score": 0.8, "reason": "事实一致"}', model=model,
                           prompt_tokens=1, completion_tokens=1, latency_ms=1)

    monkeypatch.setattr(adapter, "complete", fake_complete)
    score, reason = await assessor.judge_accuracy("q", "r")
    assert score == 0.8
    assert reason == "事实一致"


async def test_judge_accuracy_bad_output_returns_none():
    config = _config()
    assessor, _ = _assessor(config)
    # mock provider 回显非 JSON → 解析失败按未采样处理
    score, reason = await assessor.judge_accuracy("q", "r")
    assert score is None and reason is None


async def test_negative_feedback_mandatory_check(monkeypatch):
    """差评必查：未抽中的响应暂存上下文，judge_negative_feedback 补查并出分"""
    config = _config()
    assessor, adapter = _assessor(config)

    async def fake_complete(model, messages):
        return ModelResult(text='{"score": 0.2, "reason": "存在事实错误"}', model=model,
                           prompt_tokens=1, completion_tokens=1, latency_ms=1)

    monkeypatch.setattr(adapter, "complete", fake_complete)
    await assessor.assess("查询", "回答" * 100, "explain", "test", feedback_token="tok2")
    result = await assessor.judge_negative_feedback("tok2")
    assert result is not None
    assert result.accuracy == 0.2
    assert result.accuracy_sampled is True
    # 上下文已消费，重复调用返回 None
    assert await assessor.judge_negative_feedback("tok2") is None


# ---------- 管线集成 ----------


async def test_pipeline_returns_quality_score(db, store):
    config = _config()
    embedding = EmbeddingService(config)
    adapter = ModelAdapter(ModelRegistry(config), config)
    quality = QualityAssessor(embedding, adapter, config)
    retriever = KnowledgeRetriever(db, config, embedding=embedding)
    pipeline = Pipeline(db, config, retriever, adapter, quality=quality, store=store)

    resp = await pipeline.run(RSIRequest(user_id="u1", raw_input="如何使用本工具？"))
    assert resp.status == "success"
    assert resp.quality_score is not None
    assert 0.0 <= resp.quality_score <= 1.0

    # 日志终态已写入 quality_score
    rows = [e for e in iter_events(store.rsi_dir) if e.get("token") == resp.feedback_token]
    scores = [e.get("quality_score") for e in rows if e.get("quality_score") is not None]
    assert scores
    assert scores[-1] == pytest.approx(resp.quality_score)


async def test_feedback_worker_negative_rating_rechecks(db, store, monkeypatch):
    """端到端：rating ≤ 2 反馈触发 accuracy 必查并回写日志 quality_score"""
    config = _config()
    embedding = EmbeddingService(config)
    adapter = ModelAdapter(ModelRegistry(config), config)
    quality = QualityAssessor(embedding, adapter, config)
    retriever = KnowledgeRetriever(db, config, embedding=embedding)
    pipeline = Pipeline(db, config, retriever, adapter, quality=quality, store=store)

    resp = await pipeline.run(RSIRequest(user_id="u1", raw_input="解释架构设计"))
    old_score = resp.quality_score

    async def fake_complete(model, messages):
        return ModelResult(text='{"score": 0.1, "reason": "错误"}', model=model,
                           prompt_tokens=1, completion_tokens=1, latency_ms=1)

    monkeypatch.setattr(adapter, "complete", fake_complete)

    worker = FeedbackWorker(store, quality=quality)
    await worker._process(ImplicitEvent(feedback_token=resp.feedback_token, action="rejected", rating=1))

    rows = [
        e for e in iter_events(store.rsi_dir)
        if e.get("token") == resp.feedback_token and e.get("quality_score") is not None
    ]
    assert rows
    assert rows[-1]["quality_score"] != pytest.approx(old_score)  # 含 accuracy=0.1 后分数变化
