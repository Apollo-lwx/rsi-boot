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
    assert alpha == pytest.approx(3.0)  # 先验 1 + reward 2
    assert beta == pytest.approx(1.0)


async def test_worker_applies_negative_reward_with_rating(store):
    sid = _write_arm(store)
    token, _ = await _make_log(store)
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        # rejected(-1) + rating 1(-1) = -2 → beta += 2
        worker.submit(ImplicitEvent(feedback_token=token, action="rejected", rating=1))
        await _drain(worker)
    finally:
        await worker.stop()
    alpha, beta = _arm_alpha_beta(store, sid)
    assert alpha == pytest.approx(1.0)
    assert beta == pytest.approx(3.0)


async def test_worker_ignored_zero_reward_no_update(store):
    sid = _write_arm(store)
    token, _ = await _make_log(store)
    worker = FeedbackWorker(store=store)
    worker.start()
    try:
        worker.submit(ImplicitEvent(feedback_token=token, action="ignored"))
        await _drain(worker)
    finally:
        await worker.stop()
    assert _arm_alpha_beta(store, sid) == (1.0, 1.0)


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
