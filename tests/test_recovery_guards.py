"""任务 0.2 中断对账：调度幂等守卫、归档去重、audit 中断检测、promote 重跑安全。

每行对账表一个测试：中断/重启后再跑不产生重复副作用。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rsi_boot.learning.audit_store import audit_finish, audit_start
from rsi_boot.memory.logstore import append_event, archive_expired_events
from rsi_boot.memory.store import MemoryStore
from rsi_boot.scheduler.manager import SchedulerManager


def _ts(days_ago: float = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- 调度器 last-run 守卫 ----------

def _manager(store: MemoryStore) -> SchedulerManager:
    return SchedulerManager(store.rsi_dir / "archive", store=store)


@pytest.mark.asyncio
async def test_run_due_executes_once_per_interval(store):
    manager = _manager(store)
    calls: list[str] = []

    async def task() -> None:
        calls.append("ran")

    await manager._run_due("recall_arms", 7 * 24 * 3600, task)
    await manager._run_due("recall_arms", 7 * 24 * 3600, task)
    assert calls == ["ran"]


@pytest.mark.asyncio
async def test_run_due_retries_after_failure(store):
    """失败不记录：中断/异常的任务在下次到期（含重启首跑）重试。"""
    manager = _manager(store)
    calls: list[str] = []

    async def failing() -> None:
        calls.append("tried")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await manager._run_due("profiles", 60, failing)
    with pytest.raises(RuntimeError):
        await manager._run_due("profiles", 60, failing)
    assert calls == ["tried", "tried"]


@pytest.mark.asyncio
async def test_run_due_again_after_interval(store, monkeypatch):
    manager = _manager(store)
    manager._mark_run("event_archive")
    assert not manager._due("event_archive", 60)
    # 把记录改到过去 → 到期
    path = store.rsi_dir / "state" / "scheduler-runs.yaml"
    old = datetime.now(timezone.utc) - timedelta(seconds=120)
    path.write_text(f"event_archive: {old.isoformat()}\n", encoding="utf-8")
    assert manager._due("event_archive", 60)


def test_due_tolerates_corrupt_state(store):
    """last-run 文件损坏 = 无记录（放行执行），不阻断调度。"""
    path = store.rsi_dir / "state" / "scheduler-runs.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not yaml", encoding="utf-8")
    manager = _manager(store)
    assert manager._due("recall_arms", 7 * 24 * 3600)


def test_guard_without_store_keeps_legacy_behavior(tmp_path):
    """无 store（DB 运行时）无持久化位置：保持旧行为，每次都到期。"""
    manager = SchedulerManager(tmp_path / "archive")
    assert manager._due("profiles", 24 * 3600)
    manager._mark_run("profiles")  # 静默无操作
    assert manager._due("profiles", 24 * 3600)


# ---------- 事件归档去重（归档先于删除的崩溃窗口） ----------

def test_archive_rerun_after_crash_no_duplicates(store, tmp_path):
    """模拟「已追加归档、未删源」中断：恢复后重跑，归档中同一事件只出现一次。"""
    archive = tmp_path / "archive"
    append_event(store.rsi_dir, {"id": "a" * 32, "ts": _ts(100), "kind": "recall", "task": "t"})
    append_event(store.rsi_dir, {"id": "b" * 32, "ts": _ts(1), "kind": "recall", "task": "t"})

    assert archive_expired_events(store.rsi_dir, archive, retention_days=90) == 1
    # 崩溃模拟：归档已写，源文件恢复成「未删除」状态
    events_path = store.rsi_dir / "logs" / "events.jsonl"
    events_path.write_text(
        json.dumps({"id": "a" * 32, "ts": _ts(100), "kind": "recall"}) + "\n"
        + json.dumps({"id": "b" * 32, "ts": _ts(1), "kind": "recall"}) + "\n",
        encoding="utf-8",
    )
    archive_expired_events(store.rsi_dir, archive, retention_days=90)

    month = (datetime.now(timezone.utc) - timedelta(days=100)).strftime("%Y%m")
    rows = [
        json.loads(line)
        for line in (archive / f"{month}.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [r["id"] for r in rows].count("a" * 32) == 1


# ---------- audit 会话中断检测 ----------

def test_audit_start_flags_unclosed_session(store):
    first = audit_start(store, {"scope": "session"}, "zh")
    assert first["status"] == "success"
    assert "reopened_unclosed" not in first["data"]
    second = audit_start(store, {"scope": "session"}, "zh")
    assert second["data"]["reopened_unclosed"] is True
    audit_finish(store, {"scope": "session"}, "zh")
    third = audit_start(store, {"scope": "session"}, "zh")
    assert "reopened_unclosed" not in third["data"]


# ---------- promote 重跑安全（distilled_from 边 = outward action） ----------

def test_promote_double_run_writes_nothing(store):
    """_already_distilled 防重钉住：第二次 promote 不再写 pending。"""
    from rsi_boot.learning.promote import promote_items
    from tests.test_promote import _feedback_successes, _pattern

    _pattern(store, "a" * 32, "select-star", "sql")
    _feedback_successes(store.rsi_dir, "select-star", "sql", 3)

    first = promote_items(store, {}, "zh")
    second = promote_items(store, {}, "zh")
    assert first["data"]["written"], "首次应有产出"
    assert second["data"]["written"] == [], "重跑不得重复写"
