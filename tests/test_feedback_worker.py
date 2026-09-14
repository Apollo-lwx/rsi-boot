"""P2.5 隐式反馈与异步反馈处理测试（§4.2）"""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from rsi_boot.api.tools import feedback_tool
from rsi_boot.core.models import generate_feedback_token
from rsi_boot.feedback.implicit_tracker import (
    ACTION_REWARD,
    FeedbackWorker,
    ImplicitEvent,
    diff_ratio,
    rating_reward,
)
from rsi_boot.services.log_service import LogService

SECRET = "test-secret"


async def _make_log(db, user_id="u1", project_id="p1", intent="debug", strategy="s1"):
    """插入一条 success 日志并返回 (feedback_token, request_id)"""
    from rsi_boot.core.models import RSIRequest

    request = RSIRequest(user_id=user_id, project_id=project_id, raw_input="帮我 debug")
    token = generate_feedback_token(request.request_id, user_id, SECRET)
    logs = LogService(db)
    log_id = await logs.insert_pending(request, token)
    await logs.finalize(log_id, status="success", intent=intent, intent_confidence=0.9,
                        strategy_name=strategy, model_name="mock", latency_ms=5)
    return token, str(request.request_id)


async def _add_strategy(db, project_id="p1", intent="debug", name="s1"):
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    sid = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO strategy_configs (id, project_id, intent, strategy_name, model_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'mock', ?, ?)",
        (sid, project_id, intent, name, now, now),
    )
    await conn.commit()
    return sid


async def _get_alpha_beta(db, sid):
    conn = await db.connect()
    async with conn.execute("SELECT alpha, beta FROM strategy_configs WHERE id = ?", (sid,)) as cur:
        row = await cur.fetchone()
    return row["alpha"], row["beta"]


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


async def test_worker_applies_positive_reward(db):
    sid = await _add_strategy(db)
    token, _ = await _make_log(db)
    worker = FeedbackWorker(db)
    worker.start()
    try:
        worker.submit(ImplicitEvent(feedback_token=token, action="accepted"))
        await _drain(worker)
    finally:
        await worker.stop()
    alpha, beta = await _get_alpha_beta(db, sid)
    assert alpha == pytest.approx(3.0)  # 先验 1 + reward 2
    assert beta == pytest.approx(1.0)


async def test_worker_applies_negative_reward_with_rating(db):
    sid = await _add_strategy(db)
    token, _ = await _make_log(db)
    worker = FeedbackWorker(db)
    worker.start()
    try:
        # rejected(-1) + rating 1(-1) = -2 → beta += 2
        worker.submit(ImplicitEvent(feedback_token=token, action="rejected", rating=1))
        await _drain(worker)
    finally:
        await worker.stop()
    alpha, beta = await _get_alpha_beta(db, sid)
    assert alpha == pytest.approx(1.0)
    assert beta == pytest.approx(3.0)


async def test_worker_ignored_zero_reward_no_update(db):
    sid = await _add_strategy(db)
    token, _ = await _make_log(db)
    worker = FeedbackWorker(db)
    worker.start()
    try:
        worker.submit(ImplicitEvent(feedback_token=token, action="ignored"))
        await _drain(worker)
    finally:
        await worker.stop()
    assert await _get_alpha_beta(db, sid) == (1.0, 1.0)


async def test_worker_unknown_token_skipped(db):
    worker = FeedbackWorker(db)
    worker.start()
    try:
        worker.submit(ImplicitEvent(feedback_token="no-such-token", action="accepted"))
        await _drain(worker)  # 不抛异常
    finally:
        await worker.stop()


async def test_queue_full_drops_event(db):
    worker = FeedbackWorker(db, maxsize=1)
    assert worker.submit(ImplicitEvent(feedback_token="t1", action="accepted")) is True
    assert worker.submit(ImplicitEvent(feedback_token="t2", action="accepted")) is False


# ---------- 工具层（隐式上报协议） ----------


async def test_tool_accepts_implicit_actions(db):
    token, _ = await _make_log(db)
    logs = LogService(db)
    worker = FeedbackWorker(db)
    worker.start()
    try:
        result = await feedback_tool.handle(
            logs, SECRET, {"feedback_token": token, "action": "copied"}, worker=worker
        )
        assert result["status"] == "ok"
        assert result["queued"] is True
        await _drain(worker)
    finally:
        await worker.stop()

    conn = await db.connect()
    async with conn.execute("SELECT feedback_action FROM interaction_logs WHERE feedback_token = ?", (token,)) as cur:
        row = await cur.fetchone()
    assert row["feedback_action"] == "copied"


async def test_tool_rejects_unknown_action(db):
    token, _ = await _make_log(db)
    result = await feedback_tool.handle(LogService(db), SECRET, {"feedback_token": token, "action": "liked"})
    assert result["status"] == "error"
    assert result.get("code") == "invalid"


async def test_tool_missing_token_has_not_found_code(db):
    result = await feedback_tool.handle(
        LogService(db), SECRET, {"feedback_token": "no-such-token", "action": "accepted"},
    )
    assert result["status"] == "error"
    assert result.get("code") == "not_found"


async def test_tool_modified_with_content(db):
    token, _ = await _make_log(db)
    result = await feedback_tool.handle(
        LogService(db), SECRET,
        {"feedback_token": token, "action": "modified", "modified_content": "改后的内容"},
    )
    assert result["status"] == "ok"


async def test_file_runtime_rejected_comment_writes_pending_prohibition(tmp_path):
    """File-runtime FeedbackWorker rejected+comment must extract pending YAML (not candidates-only)."""
    from rsi_boot.core.models import RSIRequest
    from rsi_boot.memory.store import MemoryStore

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
    from rsi_boot.core.models import RSIRequest
    from rsi_boot.memory.store import MemoryStore

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
    from rsi_boot.core.models import RSIRequest
    from rsi_boot.memory.store import MemoryStore

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
