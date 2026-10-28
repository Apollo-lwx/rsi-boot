"""P2.5 隐式反馈与异步反馈处理测试（§4.2）"""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
import yaml

from rsi_boot.api.tools import feedback_tool
from rsi_boot.core.models import RSIRequest, generate_feedback_token
from rsi_boot.feedback.implicit_tracker import (
    ACTION_REWARD,
    FeedbackWorker,
    ImplicitEvent,
    diff_ratio,
    rating_reward,
)
from rsi_boot.memory.logstore import append_event, iter_events
from rsi_boot.memory.store import MemoryStore
from rsi_boot.services.log_service import LogService

SECRET = "test-secret"


def _write_arm(store: MemoryStore, name="s1", alpha=1.0, beta=1.0) -> str:
    sid = uuid.uuid4().hex
    dest = store.rsi_dir / "state" / "arms.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = [{
        "id": sid,
        "name": name,
        "intent": "debug",
        "alpha": alpha,
        "beta": beta,
        "active": True,
    }]
    dest.write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return sid


def _arm_alpha_beta(store: MemoryStore, sid: str) -> tuple[float, float]:
    payload = yaml.safe_load((store.rsi_dir / "state" / "arms.yaml").read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else list((payload or {}).get("arms") or [])
    row = next(r for r in rows if r.get("id") == sid)
    return float(row["alpha"]), float(row["beta"])


async def _make_log(store, user_id="u1", project_id="p1", intent="debug", strategy="s1"):
    """写入一条 success 召回事件并返回 (feedback_token, request_id)"""
    request = RSIRequest(user_id=user_id, project_id=project_id, raw_input="帮我 debug")
    token = generate_feedback_token(request.request_id, user_id, SECRET)
    log_id = uuid.uuid4().hex
    append_event(store.rsi_dir, {
        "id": log_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "recall",
        "task": request.raw_input,
        "token": token,
        "status": "success",
        "project_id": project_id,
        "user_id": user_id,
        "request_id": str(request.request_id),
        "intent": intent,
        "arm": strategy,
        "retrieved": [],
    })
    return token, str(request.request_id)


async def _drain(worker: FeedbackWorker):
    await asyncio.wait_for(worker.queue.join(), timeout=5)


# ---------- 纯函数 ----------


def test_diff_ratio():
    assert diff_ratio("abc", "abc") == 0.0
    assert diff_ratio("", "x") == 1.0
    assert 0.0 < diff_ratio("hello world", "hello there world") < 0.5


def test_reward_mapping():
    """Spec v3.0 §4.7：accepted/copied/referenced=+2，modified=-0.5，rejected=-1，ignored=0"""
    assert ACTION_REWARD["accepted"] == 2.0
    assert ACTION_REWARD["copied"] == 2.0
    assert ACTION_REWARD["referenced"] == 2.0
    assert ACTION_REWARD["modified"] == -0.5
    assert ACTION_REWARD["rejected"] == -1.0
    assert ACTION_REWARD["ignored"] == 0.0
    assert rating_reward(5) == 1.0
    assert rating_reward(3) == 0.0
    assert rating_reward(1) == -1.0
    assert rating_reward(None) == 0.0


# ---------- FeedbackWorker ----------


async def test_worker_applies_positive_reward(store):
    sid = _write_arm(store)
    token, _ = await _make_log(store)
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        worker.submit(ImplicitEvent(feedback_token=token, action="accepted"))
        await _drain(worker)
    finally:
        await worker.stop()
    alpha, beta = _arm_alpha_beta(store, sid)
    assert alpha == pytest.approx(2.0)  # 先验 1 + 一次成功
    assert beta == pytest.approx(1.0)


async def test_worker_applies_negative_reward_with_rating(store):
    sid = _write_arm(store)
    token, _ = await _make_log(store)
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        # 拒绝再叠加低分仍是一次失败
        worker.submit(ImplicitEvent(feedback_token=token, action="rejected", rating=1))
        await _drain(worker)
    finally:
        await worker.stop()
    alpha, beta = _arm_alpha_beta(store, sid)
    assert alpha == pytest.approx(1.0)
    assert beta == pytest.approx(2.0)


async def test_worker_ignored_counts_in_denominator(store):
    sid = _write_arm(store)
    token, _ = await _make_log(store)
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        worker.submit(ImplicitEvent(feedback_token=token, action="ignored"))
        await _drain(worker)
    finally:
        await worker.stop()
    alpha, beta = _arm_alpha_beta(store, sid)
    assert alpha == pytest.approx(1.0)
    assert beta == pytest.approx(2.0)


async def test_reward_is_one_trial_and_ignore_counts(store):
    """一次反馈只计入一次试验：采纳 alpha+1，拒绝叠加低分仍是 beta+1，忽略也让 beta+1。"""
    sid = _write_arm(store)
    token, _ = await _make_log(store)
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        worker.submit(ImplicitEvent(feedback_token=token, action="accepted"))
        await _drain(worker)
    finally:
        await worker.stop()
    alpha, beta = _arm_alpha_beta(store, sid)
    assert alpha == pytest.approx(2.0)
    assert beta == pytest.approx(1.0)

    sid = _write_arm(store)
    token, _ = await _make_log(store)
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        worker.submit(ImplicitEvent(feedback_token=token, action="rejected", rating=1))
        await _drain(worker)
    finally:
        await worker.stop()
    alpha, beta = _arm_alpha_beta(store, sid)
    assert alpha == pytest.approx(1.0)
    assert beta == pytest.approx(2.0)

    sid = _write_arm(store)
    token, _ = await _make_log(store)
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        worker.submit(ImplicitEvent(feedback_token=token, action="ignored"))
        await _drain(worker)
    finally:
        await worker.stop()
    alpha, beta = _arm_alpha_beta(store, sid)
    assert alpha == pytest.approx(1.0)
    assert beta == pytest.approx(2.0)


def test_arms_yaml_decays_toward_prior(store):
    """arms.yaml 与策略表同一公式：α ← 1+(α-1)×0.9，β 同理；先验不动，停用臂不动。"""
    from rsi_boot.strategy.recall import decay_arms

    moved = uuid.uuid4().hex
    prior = uuid.uuid4().hex
    stopped = uuid.uuid4().hex
    dest = store.rsi_dir / "state" / "arms.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": moved, "name": "recall-generous", "intent": "recall", "alpha": 11.0, "beta": 3.0, "active": True},
        {"id": prior, "name": "recall-balanced", "intent": "other", "alpha": 1.0, "beta": 1.0, "active": True},
        {"id": stopped, "name": "recall-conservative", "intent": "recall", "alpha": 11.0, "beta": 3.0, "active": False},
    ]
    dest.write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert decay_arms(dest) == 1
    alpha, beta = _arm_alpha_beta(store, moved)
    assert alpha == pytest.approx(1 + 10 * 0.9)
    assert beta == pytest.approx(1 + 2 * 0.9)
    assert _arm_alpha_beta(store, prior) == (1.0, 1.0)
    assert _arm_alpha_beta(store, stopped) == (11.0, 3.0)


async def test_weekly_hook_decays_arms_yaml(store):
    from rsi_boot.scheduler.manager import SchedulerManager

    sid = _write_arm(store, alpha=11.0, beta=3.0)
    await SchedulerManager(store.rsi_dir / "archive", store=store)._decay_recall_arms()
    alpha, beta = _arm_alpha_beta(store, sid)
    assert alpha == pytest.approx(1 + 10 * 0.9)
    assert beta == pytest.approx(1 + 2 * 0.9)


def test_eliminate_arms_and_spare_last_recall(store):
    from rsi_boot.strategy.recall import eliminate_arms

    poor = uuid.uuid4().hex
    poorer = uuid.uuid4().hex
    healthy = uuid.uuid4().hex
    dest = store.rsi_dir / "state" / "arms.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": poor, "name": "recall-a", "intent": "recall", "alpha": 1.0, "beta": 9.0,
         "exposure": 100, "active": True},
        {"id": poorer, "name": "recall-b", "intent": "recall", "alpha": 1.0, "beta": 19.0,
         "exposure": 120, "active": True},
        {"id": healthy, "name": "recall-c", "intent": "recall", "alpha": 8.0, "beta": 2.0,
         "exposure": 100, "active": True},
    ]
    dest.write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert eliminate_arms(dest) == 2
    loaded = {row["id"]: row for row in yaml.safe_load(dest.read_text(encoding="utf-8"))}
    assert loaded[poor]["active"] is False
    assert loaded[poorer]["active"] is False
    assert loaded[healthy]["active"] is True

    only = [
        {"id": poor, "name": "recall-a", "intent": "recall", "alpha": 1.0, "beta": 9.0,
         "exposure": 100, "active": True},
        {"id": poorer, "name": "recall-b", "intent": "recall", "alpha": 1.0, "beta": 19.0,
         "exposure": 100, "active": True},
    ]
    dest.write_text(yaml.safe_dump(only, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert eliminate_arms(dest) == 1
    kept = [row for row in yaml.safe_load(dest.read_text(encoding="utf-8")) if row["active"]]
    assert len(kept) == 1
    assert kept[0]["id"] == poor


async def test_worker_unknown_token_skipped(store):
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        worker.submit(ImplicitEvent(feedback_token="no-such-token", action="accepted"))
        await _drain(worker)  # 不抛异常
    finally:
        await worker.stop()


async def test_queue_full_drops_event(store):
    worker = FeedbackWorker(store=store, maxsize=1)
    assert worker.submit(ImplicitEvent(feedback_token="t1", action="accepted")) is True
    assert worker.submit(ImplicitEvent(feedback_token="t2", action="accepted")) is False


# ---------- 工具层（隐式上报协议） ----------


async def test_tool_accepts_implicit_actions(store):
    token, _ = await _make_log(store)
    logs = LogService(store)
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        result = await feedback_tool.handle(
            logs, SECRET, {"feedback_token": token, "action": "copied"}, worker=worker
        )
        assert result["status"] == "success"
        assert result["queued"] is True
        await _drain(worker)
    finally:
        await worker.stop()

    fb = [e for e in iter_events(store.rsi_dir) if e.get("kind") == "feedback" and e.get("token") == token]
    assert fb and fb[-1]["action"] == "copied"


async def test_tool_rejects_unknown_action(store):
    token, _ = await _make_log(store)
    result = await feedback_tool.handle(LogService(store), SECRET, {"feedback_token": token, "action": "liked"})
    assert result["status"] == "error"
    assert result.get("code") == "invalid"


async def test_tool_missing_token_has_not_found_code(store):
    result = await feedback_tool.handle(
        LogService(store), SECRET, {"feedback_token": "no-such-token", "action": "accepted"},
    )
    assert result["status"] == "error"
    assert result.get("code") == "not_found"


async def test_tool_modified_with_content(store):
    token, _ = await _make_log(store)
    result = await feedback_tool.handle(
        LogService(store), SECRET,
        {"feedback_token": token, "action": "modified", "modified_content": "改后的内容"},
    )
    assert result["status"] == "success"


async def test_file_runtime_rejected_comment_writes_pending_prohibition(tmp_path):
    """File-runtime FeedbackWorker rejected+comment must extract pending YAML (not candidates-only)."""
    store = MemoryStore(tmp_path / ".rsi")
    logs = LogService(store=store)
    request = RSIRequest(user_id="u1", project_id="p1", raw_input="写查询")
    token = "tok-file-reject-extract"
    log_id = await logs.insert_pending(request, token)
    await logs.finalize(
        log_id, status="success", intent="recall",
        strategy_name="recall-balanced",
        retrieved_tags=["a" * 32],
        latency_ms=1,
    )
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        worker.submit(ImplicitEvent(
            feedback_token=token, action="rejected", comment="不要再用 SELECT *",
        ))
        await _drain(worker)
    finally:
        await worker.stop()

    pending = list((store.rsi_dir / "memory" / "pending").rglob("*.yaml"))
    assert pending
    rels = [p.relative_to(store.rsi_dir).as_posix() for p in pending]
    assert any(
        r.startswith("memory/pending/prohibitions") or r.startswith("memory/pending/conventions")
        for r in rels
    )
    cand = store.rsi_dir / "logs" / "candidates.jsonl"
    assert cand.is_file()
    lines = [ln for ln in cand.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1


async def test_file_runtime_modified_below_threshold_does_not_extract(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    logs = LogService(store=store)
    request = RSIRequest(user_id="u1", project_id="p1", raw_input="hello world")
    token = "tok-file-mod-small"
    log_id = await logs.insert_pending(request, token)
    await logs.finalize(
        log_id, status="success", intent="recall",
        strategy_name="recall-balanced", retrieved_tags=["b" * 32], latency_ms=1,
    )
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        worker.submit(ImplicitEvent(
            feedback_token=token, action="modified",
            modified_content="hello world!",
        ))
        await _drain(worker)
    finally:
        await worker.stop()
    pending = list((store.rsi_dir / "memory" / "pending").rglob("*.yaml")) if (
        store.rsi_dir / "memory" / "pending"
    ).exists() else []
    assert pending == []


async def test_file_runtime_rejected_without_comment_does_not_extract(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    logs = LogService(store=store)
    request = RSIRequest(user_id="u1", project_id="p1", raw_input="写查询")
    token = "tok-file-reject-empty"
    log_id = await logs.insert_pending(request, token)
    await logs.finalize(
        log_id, status="success", intent="recall",
        strategy_name="recall-balanced", retrieved_tags=["c" * 32], latency_ms=1,
    )
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        worker.submit(ImplicitEvent(feedback_token=token, action="rejected"))
        await _drain(worker)
    finally:
        await worker.stop()
    pending = list((store.rsi_dir / "memory" / "pending").rglob("*.yaml")) if (
        store.rsi_dir / "memory" / "pending"
    ).exists() else []
    assert pending == []


async def test_negative_rating_judges_excerpt_when_cache_empty(store):
    from types import SimpleNamespace

    token, _ = await _make_log(store)
    append_event(store.rsi_dir, {
        "id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "recall",
        "token": token,
        "task": "怎么查账单",
        "excerpt": "select * from bill",
        "retrieved": [],
        "status": "success",
    })

    class _Quality:
        def __init__(self):
            self.seen = None

        async def judge_negative_feedback(self, feedback_token):
            return None

        async def judge_text(self, query, response):
            self.seen = (query, response)
            return SimpleNamespace(quality_score=0.25)

    quality = _Quality()
    worker = FeedbackWorker(store=store, quality=quality)
    await worker._process(ImplicitEvent(feedback_token=token, action="ignored", rating=1))
    assert quality.seen[0] == "怎么查账单"
    assert "select * from bill" in quality.seen[1]
    scores = [
        ev.get("quality_score")
        for ev in iter_events(store.rsi_dir)
        if ev.get("kind") == "audit" and ev.get("token") == token
    ]
    assert 0.25 in scores
