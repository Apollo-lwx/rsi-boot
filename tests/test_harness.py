"""P3.6 Harness 自我改进测试（§3.9）：失败挖掘 / 提案生成与速率上限 / 回归门禁 /
生命周期流转 / 观察期巡检 / Genome 快照 / 技能槽"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from rsi_boot.knowledge.embedding import EmbeddingService
from rsi_boot.learning.proposal_engine import (
    MAX_PROPOSALS_PER_ROUND,
    ProposalEngine,
)
from rsi_boot.learning.regression_gate import RegressionGate
from rsi_boot.learning.skill_loader import SkillLoader, parse_skill_md
from rsi_boot.learning.snapshot_store import SnapshotStore
from rsi_boot.memory.logstore import append_event
from rsi_boot.model.adapter import ModelAdapter
from rsi_boot.model.providers.base import ModelResult
from rsi_boot.model.registry import ModelRegistry


def _config():
    return {
        "model": {"default": "mock", "timeout_s": 5, "max_retries": 0},
        "retrieval": {"top_n": 5, "token_budget": 2000},
        "embedding": {"provider": "mock"},
        "harness": {"auto_apply": False},
    }


def _engine(store, tmp_path, monkeypatch=None, llm_payload=None):
    """文件运行时 generate 走模板化路径；llm_payload 仅保留给门禁/适配器桩。"""
    config = _config()
    if llm_payload is not None:
        config["enhance"] = {"proposal_llm": True, "model": {"default": "mock"}}
    adapter = ModelAdapter(ModelRegistry(config), config)
    if monkeypatch is not None and llm_payload is not None:
        async def fake_complete(model, messages):
            return ModelResult(text=json.dumps(llm_payload, ensure_ascii=False), model=model,
                               prompt_tokens=1, completion_tokens=1, latency_ms=1)
        monkeypatch.setattr(adapter, "complete", fake_complete)
    snapshots = SnapshotStore(store=store, rsi_home=tmp_path / ".rsi")
    return ProposalEngine(
        store=store, adapter=adapter, config=config, snapshots=snapshots, rsi_home=tmp_path / ".rsi",
    ), snapshots


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_negative_logs(store, intent="debug", strategy="s1", n=6, project="p1", comment=None,
                        retrieved=None):
    """造负信号：rejected 反馈的召回事件 n 条（comment 非空时携带评语）"""
    doc = retrieved or ("a" * 32)
    ts = _now_iso()
    for i in range(n):
        token = f"tok-{uuid.uuid4().hex[:8]}"
        lid = uuid.uuid4().hex
        append_event(store.rsi_dir, {
            "id": lid, "kind": "recall", "token": token, "ts": ts,
            "task": f"问题{i}：如何修复崩溃？", "retrieved": [doc],
            "arm": strategy, "intent": intent, "project_id": project,
            "status": "success", "user_id": "u1",
        })
        append_event(store.rsi_dir, {
            "id": uuid.uuid4().hex, "kind": "feedback", "token": token, "ts": ts,
            "action": "rejected", "comment": comment,
        })


# ---------- 失败模式挖掘 ----------


async def test_mine_failures_buckets(store, tmp_path):
    """负信号按 retrieved 分桶；评语入桶作提案证据"""
    _seed_negative_logs(store, n=6, comment="不要用 SELECT *")
    engine, _ = _engine(store, tmp_path)
    buckets = engine._mine_store_failures("p1", days=7)
    assert len(buckets) == 1
    b = buckets[0]
    assert b.count == 6
    assert b.signals["rejected"] == 6
    assert "不要用 SELECT *" in b.comments


async def test_mine_failures_min_samples(store, tmp_path):
    """文件运行时无 sqlite 最小样本闸：3 条 rejected+comment 仍可生成禁止项提案"""
    _seed_negative_logs(store, n=3, comment="不要用 SELECT *")
    engine, _ = _engine(store, tmp_path)
    ids = await engine.generate("p1")
    assert len(ids) == 1


# ---------- 提案生成与速率上限 ----------


async def test_generate_proposals(store, tmp_path):
    for i in range(3):
        _seed_negative_logs(store, n=1, comment=f"不要用 SELECT * {i}", retrieved=f"{i:032x}")
    engine, _ = _engine(store, tmp_path)
    ids = await engine.generate("p1")
    assert len(ids) == 3
    proposals = await engine.list_proposals("p1")
    assert all(p["status"] == "proposed" for p in proposals)
    evidence = proposals[0]["evidence"]
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    assert evidence["bucket"]["intent"] == "recall"
    assert evidence["log_ids"]


async def test_generate_slot_weekly_cap(store, tmp_path):
    for i in range(3):
        _seed_negative_logs(store, n=1, comment=f"不要用 SELECT * {i}", retrieved=f"{i:032x}")
    engine, _ = _engine(store, tmp_path)
    await engine.generate("p1")  # 第一轮 3 个 knowledge 提案
    ids = await engine.generate("p1")  # 第二轮：knowledge 周上限 3 已达
    assert ids == []


async def test_generate_round_cap(store, tmp_path):
    # 非 balanced 召回臂：每桶同时产出 knowledge + strategy，单槽周上限 3 挡不住单轮上限 5
    for i in range(3):
        _seed_negative_logs(
            store, n=1, strategy="recall-generous",
            comment=f"不要用 SELECT * {i}", retrieved=f"{i:032x}",
        )
    engine, _ = _engine(store, tmp_path)
    ids = await engine.generate("p1")
    assert len(ids) == MAX_PROPOSALS_PER_ROUND


# ---------- 模板化提案（v3.0 零 Key 默认路径，§4.5） ----------


async def test_template_proposal_prohibition(store, tmp_path):
    """rejected+comment 桶 → 禁止项提案（模板化，无 LLM 调用）"""
    _seed_negative_logs(store, n=6, comment="不要用 SELECT *")
    engine, _ = _engine(store, tmp_path)
    ids = await engine.generate("p1")
    assert len(ids) == 1
    proposals = await engine.list_proposals("p1")
    p = proposals[0]
    assert p["slot"] == "knowledge" and p["action"] == "add"
    assert p["status"] == "proposed"
    payload = p["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["after"]["content_type"] == "prohibition"
    assert "不要用 SELECT *" in payload["after"]["content"]
    evidence = p["evidence"]
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    assert evidence["bucket"]["intent"] == "recall"
    assert evidence["log_ids"]


async def test_template_proposal_no_comment_no_proposal(store, tmp_path):
    """负信号桶无评语 → 无法生成禁止项提案（模板化路径不臆造内容）"""
    _seed_negative_logs(store, n=6)
    engine, _ = _engine(store, tmp_path)
    assert await engine.generate("p1") == []


# ---------- 回归门禁（RegressionGate 仍读 sqlite 回放集） ----------


async def _seed_replay_logs(db, intent="debug", n=8, project="p1", excerpt=None):
    """造回放集：成功终态日志（含响应摘录供基线评估）"""
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    excerpt = excerpt or ("修复步骤：\n1. 检查日志\n2. 定位异常\n3. 修复\n```python\nfix()\n```\n" + "详" * 60)
    for i in range(n):
        lid = uuid.uuid4().hex
        await conn.execute(
            "INSERT INTO interaction_logs (id, request_id, user_id, project_id, raw_input,"
            " intent, intent_confidence, latency_ms, status, feedback_token, response_excerpt, created_at)"
            " VALUES (?, ?, 'u1', ?, ?, ?, 0.9, 10, 'success', ?, ?, ?)",
            (lid, uuid.uuid4().hex, project, f"问题{i}：程序崩溃怎么办？", intent,
             f"tok-{lid[:8]}", excerpt, now),
        )
    await conn.commit()


async def test_gate_rejects_degraded_candidate(db, tmp_path, monkeypatch):
    """候选响应质量退化（空响应）→ 门禁必须拒绝（种子退化集拦截）"""
    await _seed_replay_logs(db, n=8)
    config = _config()
    embedding = EmbeddingService(config)
    adapter = ModelAdapter(ModelRegistry(config), config)
    gate = RegressionGate(db, adapter, embedding, config)

    async def bad_complete(model, messages):
        return ModelResult(text="不知道。", model=model, prompt_tokens=1, completion_tokens=1, latency_ms=1)

    monkeypatch.setattr(adapter, "complete", bad_complete)
    proposal = {"slot": "prompt_template", "target_ref": "debug.jinja2", "action": "modify",
                "payload": {"after": {"template_content": "随意回答"}}, "intent": "debug"}
    report = await gate.evaluate(proposal)
    assert report.passed is False


async def test_gate_rejects_insufficient_samples(db, tmp_path):
    await _seed_replay_logs(db, n=2)  # < MIN_REPLAY_SAMPLES
    config = _config()
    embedding = EmbeddingService(config)
    adapter = ModelAdapter(ModelRegistry(config), config)
    gate = RegressionGate(db, adapter, embedding, config)
    proposal = {"slot": "strategy", "target_ref": "s1", "action": "modify",
                "payload": {"after": {"model_name": "mock"}}, "intent": "debug"}
    report = await gate.evaluate(proposal)
    assert report.passed is False
    assert "样本不足" in report.reason


# ---------- 生命周期：晋升 + 快照 + 回滚 ----------


def _make_approved_proposal(engine, after=None, evidence=None):
    """直接构造 approved 状态提案（绕过门禁，测晋升路径）"""
    pid = uuid.uuid4().hex
    engine._write_proposal_yaml({
        "id": pid,
        "project_id": "p1",
        "slot": "strategy",
        "target_ref": "new-strategy",
        "action": "add",
        "payload": {"after": after or {"intent": "debug", "model_name": "mock"}},
        "evidence": evidence or {},
        "status": "approved",
        "created_at": _now_iso(),
    })
    return pid


def _arm_rows(store) -> list[dict]:
    path = store.rsi_dir / "state" / "arms.yaml"
    if not path.is_file():
        return []
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return [r for r in (payload or {}).get("arms") or [] if isinstance(r, dict)]


async def test_approve_applies_and_snapshots(store, tmp_path):
    engine, snapshots = _engine(store, tmp_path)
    pid = _make_approved_proposal(engine)
    assert await engine.approve(pid) is True

    rows = _arm_rows(store)
    assert any((r.get("name") or r.get("strategy_name")) == "new-strategy" and r.get("active", True)
               for r in rows)

    versions = await snapshots.list("p1")
    assert len(versions) == 1 and versions[0]["version"] == 1
    proposals = await engine.list_proposals("p1")
    assert proposals[0]["status"] == "active"


async def test_rollback_switches_snapshot(store, tmp_path):
    engine, snapshots = _engine(store, tmp_path)
    await snapshots.capture("p1")  # v1 = 晋升前基线（空策略）
    pid = _make_approved_proposal(engine)
    assert await engine.approve(pid) is True  # v2 = 含 new-strategy

    assert await engine.rollback(pid, reason="测试回滚") is True
    rows = _arm_rows(store)
    assert not any((r.get("name") or r.get("strategy_name")) == "new-strategy" and r.get("active", True)
                   for r in rows)
    versions = {v["version"]: v["status"] for v in await snapshots.list("p1")}
    assert versions[1] == "rolled_back_to"
    proposals = await engine.list_proposals("p1")
    assert proposals[0]["status"] == "rolled_back"


# ---------- 观察期巡检 ----------


async def test_patrol_auto_rollback_on_drop(store, tmp_path):
    """文件运行时 patrol 尚未接 events.jsonl，返回空计数"""
    engine, _ = _engine(store, tmp_path)
    result = await engine.patrol("p1")
    assert result == {"patrolled": 0, "rolled_back": 0}


# ---------- Genome 快照合并语义与 bundle ----------


async def test_snapshot_inherit_semantics(store, tmp_path):
    """与上一版本一致的槽位存 inherit 标记；解析沿版本链回放"""
    snap = SnapshotStore(store=store, rsi_home=tmp_path / ".rsi")
    home = tmp_path / ".rsi"
    home.mkdir(parents=True, exist_ok=True)
    (home / "intent_rules.yaml").write_text("rules: []\n", encoding="utf-8")

    v1 = await snap.capture("p1")
    assert v1 == 1
    v2 = await snap.capture("p1")  # 无变更 → 全部 inherit
    path = store.rsi_dir / "state" / "snapshots" / "2" / "snapshot.yaml"
    slots = yaml.safe_load(path.read_text(encoding="utf-8"))["slots"]
    assert all(s.get("inherit") for s in slots.values())

    resolved = await snap._resolve("p1", v2)
    assert resolved["intent_rule"]["overlay_yaml"] == "rules: []\n"


async def test_export_bundle(store, tmp_path):
    snap = SnapshotStore(store=store, rsi_home=tmp_path / ".rsi")
    home = tmp_path / ".rsi"
    (home / "skills" / "demo").mkdir(parents=True)
    (home / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\ntrigger:\n  intents: [debug]\n---\n指令体\n", encoding="utf-8"
    )
    await snap.capture("p1")
    out = await snap.export_bundle("p1", 1, tmp_path / "bundle")
    assert out is not None
    assert (out / "genome.json").is_file()
    assert (out / "skills" / "demo" / "SKILL.md").is_file()
    manifest = json.loads((out / "genome.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1 and "skill" in manifest["slots"]


# ---------- 技能槽 ----------


def test_parse_skill_md():
    text = "---\nname: tdd\ndescription: 测试驱动\ntrigger:\n  intents: [test_gen]\n  keywords: [TDD]\n---\n先写测试。\n"
    skill = parse_skill_md(text)
    assert skill is not None
    assert skill.name == "tdd"
    assert skill.matches("test_gen", "任意输入")
    assert skill.matches(None, "来实践 TDD 吧")
    assert not skill.matches("explain", "无关输入")


def test_skill_loader_dirs(tmp_path):
    d1 = tmp_path / "a"
    d2 = tmp_path / "b"
    (d1 / "s1").mkdir(parents=True)
    (d2 / "s1").mkdir(parents=True)
    (d1 / "s1" / "SKILL.md").write_text("---\nname: s1\ntrigger: [debug]\n---\n旧版", encoding="utf-8")
    (d2 / "s1" / "SKILL.md").write_text("---\nname: s1\ntrigger: [debug]\n---\n新版", encoding="utf-8")
    loader = SkillLoader([d1, d2])
    hits = loader.match("debug", "输入")
    assert len(hits) == 1
    assert hits[0].body == "新版"  # 后目录覆盖同名
