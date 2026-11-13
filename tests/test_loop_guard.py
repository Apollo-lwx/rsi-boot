"""学习环空转守卫（借鉴 OpenMausBot runtime-contract §6 loop guard）。

验收（方案任务 1.2）：
- 连续 N 轮蒸馏无新提案且事件流无变化 → 暂停并写 loop_guard_slowed 事件；
- 新反馈事件到达（指纹变化）→ 恢复并写 loop_guard_resumed 事件；
- 守卫自己写的事件不得被当成「新反馈」（防振荡）；
- 状态文件损坏 → fail-open（正常执行，空转只是浪费，误暂停才是风险）。

变异点：阈值 off-by-one、事件写盘后不重取指纹、should_run 不查指纹，对应测试全红。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rsi_boot.learning.loop_guard import LoopGuard
from rsi_boot.memory.logstore import append_event


def _guard(store, threshold: int = 3) -> LoopGuard:
    return LoopGuard(store.rsi_dir, idle_threshold=threshold)


def _event_kinds(rsi_dir: Path) -> list[str]:
    path = rsi_dir / "logs" / "events.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line).get("kind")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _touch_feedback(rsi_dir: Path) -> None:
    append_event(rsi_dir, {"ts": "2026-11-13T00:00:00Z", "kind": "feedback", "action": "accepted"})


# ---------- 空转判定与暂停 ----------


def test_first_round_runs_and_idle_counts(store):
    guard = _guard(store)
    assert guard.should_run("harness_cycle") is True
    assert guard.record_outcome("harness_cycle", produced=0) is True  # 记空转
    assert guard.status("harness_cycle")["idle_rounds"] == 1
    assert guard.status("harness_cycle")["slowed"] is False


def test_pause_after_threshold_and_event_written_once(store):
    guard = _guard(store)
    for _ in range(3):  # threshold=3
        assert guard.should_run("harness_cycle") is True
        guard.record_outcome("harness_cycle", produced=0)
    state = guard.status("harness_cycle")
    assert state["slowed"] is True
    assert _event_kinds(store.rsi_dir).count("loop_guard_slowed") == 1

    # 暂停中跳过；slowed 事件不重复写
    assert guard.should_run("harness_cycle") is False
    assert _event_kinds(store.rsi_dir).count("loop_guard_slowed") == 1


def test_own_slowed_event_does_not_trigger_resume(store):
    """slowed 事件写盘改变事件指纹；守卫不得把自己的事件当成新反馈而振荡。"""
    guard = _guard(store)
    for _ in range(3):
        guard.should_run("harness_cycle")
        guard.record_outcome("harness_cycle", produced=0)
    assert guard.status("harness_cycle")["slowed"] is True
    # 若指纹在写事件前取值，这里会因自己写的 slowed 事件误判恢复
    assert guard.should_run("harness_cycle") is False
    assert "loop_guard_resumed" not in _event_kinds(store.rsi_dir)


# ---------- 恢复 ----------


def test_new_feedback_event_resumes(store):
    guard = _guard(store)
    for _ in range(3):
        guard.should_run("harness_cycle")
        guard.record_outcome("harness_cycle", produced=0)
    assert guard.should_run("harness_cycle") is False

    _touch_feedback(store.rsi_dir)  # 新反馈到达 → 指纹变化

    assert guard.should_run("harness_cycle") is True
    assert "loop_guard_resumed" in _event_kinds(store.rsi_dir)
    state = guard.status("harness_cycle")
    assert state["slowed"] is False
    assert state["idle_rounds"] == 0


def test_progress_resets_idle_before_threshold(store):
    guard = _guard(store)
    guard.should_run("harness_cycle")
    guard.record_outcome("harness_cycle", produced=0)
    guard.should_run("harness_cycle")
    guard.record_outcome("harness_cycle", produced=2)  # 有产出 → 清零
    state = guard.status("harness_cycle")
    assert state["idle_rounds"] == 0
    assert state["slowed"] is False


def test_event_change_counts_as_progress(store):
    """无产出但事件流有变化（新反馈还没蒸出提案）→ 算进展，不计空转。"""
    guard = _guard(store)
    guard.should_run("harness_cycle")
    guard.record_outcome("harness_cycle", produced=0)
    _touch_feedback(store.rsi_dir)
    guard.should_run("harness_cycle")
    assert guard.record_outcome("harness_cycle", produced=0) is False  # 不算空转
    assert guard.status("harness_cycle")["idle_rounds"] == 0


# ---------- 韧性 ----------


def test_corrupt_state_fails_open(store):
    path = store.rsi_dir / "state" / "loop-guard.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{{{{not yaml", encoding="utf-8")
    guard = _guard(store)
    assert guard.should_run("harness_cycle") is True  # 误暂停才是风险，fail-open
    assert guard.record_outcome("harness_cycle", produced=0) is True  # 可重新计数
    assert guard.status("harness_cycle")["idle_rounds"] == 1


def test_threshold_validation(store):
    with pytest.raises(ValueError):
        LoopGuard(store.rsi_dir, idle_threshold=0)


# ---------- 调度器集成 ----------


@pytest.mark.asyncio
async def test_manager_skips_harness_cycle_when_slowed(store):
    """暂停中 harness_cycle 不调 generate；新事件到达后恢复正常蒸馏。"""
    from rsi_boot.scheduler.manager import SchedulerManager

    class _Engine:
        def __init__(self):
            self.calls = 0

        async def generate(self, project_id):
            self.calls += 1
            return []

        async def validate_pending(self, project_id):
            return {"validated": 0}

    engine = _Engine()
    manager = SchedulerManager(
        store.rsi_dir / "archive", store=store,
        proposal_engine_provider=lambda: engine,
        project_ids=["p1"],
    )
    guard = _guard(store, threshold=1)
    guard.should_run("harness_cycle")
    guard.record_outcome("harness_cycle", produced=0)  # idle=1 ≥ 1 → 暂停

    await manager._run_harness_cycle()
    assert engine.calls == 0  # 暂停中未蒸馏

    _touch_feedback(store.rsi_dir)
    await manager._run_harness_cycle()
    assert engine.calls == 1  # 恢复后正常蒸馏
