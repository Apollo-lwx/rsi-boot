"""P3.6 Harness 自我改进测试（§3.9）：失败挖掘 / 提案生成与速率上限 / 回归门禁 /
生命周期流转 / 观察期巡检 / Genome 快照 / 技能槽"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rsi_boot.data.sqlite import SQLiteClient
from rsi_boot.knowledge.embedding import EmbeddingService
from rsi_boot.learning.failure_miner import mine_failures
from rsi_boot.learning.proposal_engine import (
    MAX_PROPOSALS_PER_ROUND,
    SLOT_WEEKLY_CAP,
    ProposalEngine,
)
from rsi_boot.learning.regression_gate import RegressionGate
from rsi_boot.learning.skill_loader import SkillLoader, parse_skill_md
from rsi_boot.learning.snapshot_store import SnapshotStore
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


def _engine(db, tmp_path, monkeypatch=None, llm_payload=None):
    """llm_payload 非空时走 LLM 提案增强路径（enhance.proposal_llm）；缺省为模板化（零 Key）"""
    config = _config()
    if llm_payload is not None:
        config["enhance"] = {"proposal_llm": True, "model": {"default": "mock"}}
    embedding = EmbeddingService(config)
    adapter = ModelAdapter(ModelRegistry(config), config)
    if monkeypatch is not None and llm_payload is not None:
        async def fake_complete(model, messages):
            return ModelResult(text=json.dumps(llm_payload, ensure_ascii=False), model=model,
                               prompt_tokens=1, completion_tokens=1, latency_ms=1)
        monkeypatch.setattr(adapter, "complete", fake_complete)
    gate = RegressionGate(db, adapter, embedding, config)
    snapshots = SnapshotStore(db, tmp_path / ".rsi")
    return ProposalEngine(db, adapter, config, gate, snapshots, tmp_path / ".rsi"), snapshots


async def _seed_negative_logs(db, intent="debug", strategy="s1", n=6, project="p1", comment=None):
    """造负信号：rejected 反馈的终态日志 n 条（comment 非空时携带评语）"""
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    for i in range(n):
        lid = uuid.uuid4().hex
        await conn.execute(
            "INSERT INTO interaction_logs (id, request_id, user_id, project_id, raw_input,"
            " intent, intent_confidence, strategy_name, latency_ms, status, feedback_token,"
            " feedback_action, feedback_comment, created_at)"
            f" VALUES (?, ?, 'u1', ?, ?, ?, 0.9, ?, 10, 'success', ?, 'rejected', ?, ?)",
            (lid, uuid.uuid4().hex, project, f"问题{i}：如何修复崩溃？", intent, strategy,
             f"tok-{lid[:8]}", comment, now),
        )
    await conn.commit()


# ---------- 失败模式挖掘 ----------


async def test_mine_failures_buckets(db):
    """v3.0 负信号源：rejected / rating≤2 / ignored（recall 反馈），评语入桶作提案证据"""
    await _seed_negative_logs(db, n=6, comment="不要用 SELECT *")
    buckets = await mine_failures(db)
    assert len(buckets) == 1
    b = buckets[0]
    assert b.intent == "debug" and b.strategy_name == "s1"
    assert b.count == 6
    assert b.signals["rejected"] == 6
    assert b.comments == ["不要用 SELECT *"] * 5  # 单桶评语证据上限 5 条


async def test_mine_failures_min_samples(db):
    await _seed_negative_logs(db, n=3)  # < 5 不成桶
    assert await mine_failures(db) == []


# ---------- 提案生成与速率上限 ----------


def _proposal_payload(n=1, slot="strategy"):
    return [
        {"slot": slot, "target_ref": f"strategy-x{i}", "action": "add",
         "after": {"intent": "debug", "model_name": "mock"}, "rationale": f"修复桶 {i}"}
        for i in range(n)
    ]


async def test_generate_proposals(db, tmp_path, monkeypatch):
    await _seed_negative_logs(db, n=6)
    engine, _ = _engine(db, tmp_path, monkeypatch, llm_payload=_proposal_payload(3))
    ids = await engine.generate("p1")
    assert len(ids) == 3  # 单桶 ≤ 3
    proposals = await engine.list_proposals("p1")
    assert all(p["status"] == "proposed" for p in proposals)
    # 证据绑定失败桶（Self-Harness 约束）
    evidence = json.loads(proposals[0]["evidence"])
    assert evidence["bucket"]["intent"] == "debug"
    assert evidence["log_ids"]


async def test_generate_slot_weekly_cap(db, tmp_path, monkeypatch):
    await _seed_negative_logs(db, n=6)
    engine, _ = _engine(db, tmp_path, monkeypatch, llm_payload=_proposal_payload(3))
    await engine.generate("p1")  # 第一轮 3 个 strategy 提案
    ids = await engine.generate("p1")  # 第二轮：strategy 周上限 3 已达
    assert ids == []


async def test_generate_round_cap(db, tmp_path, monkeypatch):
    # 3 个失败桶 × 3 提案（跨 3 槽位避开槽位周上限）= 9 > 单轮上限 5
    for intent, strategy in [("debug", "s1"), ("explain", "s2"), ("code_gen", "s3")]:
        await _seed_negative_logs(db, intent=intent, strategy=strategy, n=5)
    multi_slot_payload = [
        {"slot": slot, "target_ref": f"t-{slot}", "action": "add",
         "after": {"intent": "debug", "model_name": "mock", "title": "t", "content": "c",
                   "skill_md": "---\nname: x\n---\nbody"}, "rationale": "r"}
        for slot in ("strategy", "knowledge", "skill")
    ]
    engine, _ = _engine(db, tmp_path, monkeypatch, llm_payload=multi_slot_payload)
    ids = await engine.generate("p1")
    assert len(ids) == MAX_PROPOSALS_PER_ROUND


# ---------- 模板化提案（v3.0 零 Key 默认路径，§4.5） ----------


async def test_template_proposal_prohibition(db, tmp_path):
    """rejected+comment 桶 → 禁止项提案（模板化，无 LLM 调用）"""
    await _seed_negative_logs(db, n=6, comment="不要用 SELECT *")
    engine, _ = _engine(db, tmp_path)  # 无 llm_payload → 模板化路径
    ids = await engine.generate("p1")
    assert len(ids) == 1
    proposals = await engine.list_proposals("p1")
    p = proposals[0]
    assert p["slot"] == "knowledge" and p["action"] == "add"
    assert p["status"] == "proposed"
    payload = json.loads(p["payload"])
    assert payload["after"]["content_type"] == "prohibition"
    assert "不要用 SELECT *" in payload["after"]["content"]
    # 证据绑定失败桶（Self-Harness 约束不变）
    evidence = json.loads(p["evidence"])
    assert evidence["bucket"]["intent"] == "debug"
    assert evidence["log_ids"]


async def test_template_proposal_no_comment_no_proposal(db, tmp_path):
    """负信号桶无评语 → 无法生成禁止项提案（模板化路径不臆造内容）"""
    await _seed_negative_logs(db, n=6)
    engine, _ = _engine(db, tmp_path)
    assert await engine.generate("p1") == []


# ---------- 回归门禁 ----------


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
    engine, _ = _engine(db, tmp_path)

    async def bad_complete(model, messages):
        return ModelResult(text="不知道。", model=model, prompt_tokens=1, completion_tokens=1, latency_ms=1)

    monkeypatch.setattr(engine._adapter, "complete", bad_complete)
    proposal = {"slot": "prompt_template", "target_ref": "debug.jinja2", "action": "modify",
                "payload": {"after": {"template_content": "随意回答"}}, "intent": "debug"}
    report = await engine._gate.evaluate(proposal)
    assert report.passed is False


async def test_gate_rejects_insufficient_samples(db, tmp_path):
    await _seed_replay_logs(db, n=2)  # < MIN_REPLAY_SAMPLES
    engine, _ = _engine(db, tmp_path)
    proposal = {"slot": "strategy", "target_ref": "s1", "action": "modify",
                "payload": {"after": {"model_name": "mock"}}, "intent": "debug"}
    report = await engine._gate.evaluate(proposal)
    assert report.passed is False
    assert "样本不足" in report.reason


# ---------- 生命周期：晋升 + 快照 + 回滚 ----------


async def _make_approved_proposal(db, engine, monkeypatch=None, after=None, evidence=None):
    """直接构造 approved 状态提案（绕过门禁，测晋升路径）"""
    conn = await db.connect()
    pid = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO harness_proposals (id, project_id, slot, target_ref, action, payload, evidence,"
        " status, created_at) VALUES (?, 'p1', 'strategy', 'new-strategy', 'add', ?, ?, 'approved', ?)",
        (pid, json.dumps({"after": after or {"intent": "debug", "model_name": "mock"}}),
         json.dumps(evidence or {}), now),
    )
    await conn.commit()
    return pid


async def test_approve_applies_and_snapshots(db, tmp_path):
    engine, snapshots = _engine(db, tmp_path)
    pid = await _make_approved_proposal(db, engine, None)
    assert await engine.approve(pid) is True

    # 槽位已应用：strategy_configs 新增行
    conn = await db.connect()
    async with conn.execute(
        "SELECT strategy_name, is_active FROM strategy_configs WHERE project_id = 'p1'"
    ) as cur:
        rows = await cur.fetchall()
    assert any(r["strategy_name"] == "new-strategy" and r["is_active"] == 1 for r in rows)

    # 快照已捕获（v1），提案 active 且处于观察期
    versions = await snapshots.list("p1")
    assert len(versions) == 1 and versions[0]["version"] == 1
    proposals = await engine.list_proposals("p1")
    assert proposals[0]["status"] == "active"


async def test_rollback_switches_snapshot(db, tmp_path):
    engine, snapshots = _engine(db, tmp_path)
    pid = await _make_approved_proposal(db, engine, None)
    await snapshots.capture("p1")  # v1 = 晋升前基线（空策略）
    # 提案在 capture 前构造，pre_activation_version 在 approve 时记录 → 需重造
    conn = await db.connect()
    await conn.execute("DELETE FROM harness_proposals")
    await conn.commit()
    pid = await _make_approved_proposal(db, engine, None)
    assert await engine.approve(pid) is True  # v2 = 含 new-strategy

    assert await engine.rollback(pid, reason="测试回滚") is True
    # 回滚后策略行被移除，快照 v1 标记 rolled_back_to
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM strategy_configs WHERE project_id = 'p1'"
    ) as cur:
        assert (await cur.fetchone())["n"] == 0
    versions = {v["version"]: v["status"] for v in await snapshots.list("p1")}
    assert versions[1] == "rolled_back_to"
    proposals = await engine.list_proposals("p1")
    assert proposals[0]["status"] == "rolled_back"


# ---------- 观察期巡检 ----------


async def test_patrol_auto_rollback_on_drop(db, tmp_path):
    engine, snapshots = _engine(db, tmp_path)
    await snapshots.capture("p1")  # v1 基线
    pid = await _make_approved_proposal(
        db, engine, evidence={"bucket": {"intent": "debug", "strategy": "s1", "count": 6}},
    )
    await engine.approve(pid)

    # 构造采纳率恶化：晋升前 7 天 100% 采纳，晋升后 0% 采纳（各 6 条反馈）
    conn = await db.connect()
    now = datetime.now(timezone.utc)
    async with conn.execute(
        "SELECT activated_at FROM harness_proposals WHERE id = ?", (pid,)
    ) as cur:
        activated_at = (await cur.fetchone())["activated_at"]
    activated = datetime.fromisoformat(activated_at)
    for i in range(6):
        for ts, action in (
            (activated - timedelta(days=3), "accepted"),
            (activated, "rejected"),  # 窗口 [activated_at, now] 左闭区间，取晋升时刻本身
        ):
            lid = uuid.uuid4().hex
            await conn.execute(
                "INSERT INTO interaction_logs (id, request_id, user_id, project_id, raw_input,"
                " intent, latency_ms, status, feedback_token, feedback_action, created_at)"
                " VALUES (?, ?, 'u1', 'p1', 'q', 'debug', 10, 'success', ?, ?, ?)",
                (lid, uuid.uuid4().hex, f"tok-{lid[:8]}", action, ts.isoformat()),
            )
    await conn.commit()

    result = await engine.patrol("p1")
    assert result["rolled_back"] == 1
    proposals = await engine.list_proposals("p1")
    assert proposals[0]["status"] == "rolled_back"


# ---------- Genome 快照合并语义与 bundle ----------


async def test_snapshot_inherit_semantics(db, tmp_path):
    """与上一版本一致的槽位存 inherit 标记；解析沿版本链回放"""
    store = SnapshotStore(db, tmp_path / ".rsi")
    home = tmp_path / ".rsi"
    home.mkdir(parents=True)
    (home / "intent_rules.yaml").write_text("rules: []\n", encoding="utf-8")

    v1 = await store.capture("p1")
    assert v1 == 1
    v2 = await store.capture("p1")  # 无变更 → 全部 inherit
    conn = await db.connect()
    async with conn.execute(
        "SELECT slots FROM config_snapshots WHERE project_id = 'p1' AND version = 2"
    ) as cur:
        slots = json.loads((await cur.fetchone())["slots"])
    assert all(s.get("inherit") for s in slots.values())

    resolved = await store._resolve("p1", v2)
    assert resolved["intent_rule"]["overlay_yaml"] == "rules: []\n"


async def test_export_bundle(db, tmp_path):
    store = SnapshotStore(db, tmp_path / ".rsi")
    home = tmp_path / ".rsi"
    (home / "skills" / "demo").mkdir(parents=True)
    (home / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\ntrigger:\n  intents: [debug]\n---\n指令体\n", encoding="utf-8"
    )
    await store.capture("p1")
    out = await store.export_bundle("p1", 1, tmp_path / "bundle")
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
