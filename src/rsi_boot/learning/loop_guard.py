"""学习环空转守卫（借鉴 OpenMausBot runtime-contract §6 loop guard）。

问题：harness_cycle 等蒸馏任务按周期醒来，若记忆库长期无新反馈，
每轮都跑一遍「无输入 → 无产出」的空转，白白消耗并稀释日志信号。

判定（两个条件同时满足才算空转一轮）：
1. 本轮蒸馏无新提案产出；
2. 事件流指纹（events.jsonl 的 mtime+size）自上轮以来无变化。

连续 idle_threshold 轮空转 → 暂停该任务并写 loop_guard_slowed 事件；
暂停期间每轮检查一次指纹，新事件到达（指纹变化）即恢复，
写 loop_guard_resumed 事件，状态清零。

状态落 `.rsi/state/loop-guard.yaml`，随 serve 重启存活（对账语义同
scheduler-runs.yaml：状态文件损坏回退为「无状态」，即正常执行，fail-open
到「跑一轮无害」侧——空转只是浪费，误暂停才是风险）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)

_STATE_REL = ("state", "loop-guard.yaml")
_EVENTS_REL = ("logs", "events.jsonl")

DEFAULT_IDLE_THRESHOLD = 3


class LoopGuard:
    """单个 .rsi 库的学习环空转守卫；threshold 为连续空转多少轮后暂停。"""

    def __init__(self, rsi_dir: Path, idle_threshold: int = DEFAULT_IDLE_THRESHOLD):
        if idle_threshold < 1:
            raise ValueError("idle_threshold 必须 ≥ 1")
        self._rsi_dir = Path(rsi_dir)
        self._threshold = idle_threshold

    # ---- 指纹与状态 ----

    def signal(self) -> str:
        """事件流指纹：events.jsonl 的 mtime_ns+size；文件不存在为空串。"""
        path = self._rsi_dir.joinpath(*_EVENTS_REL)
        try:
            st = path.stat()
        except OSError:
            return ""
        return f"{st.st_mtime_ns}:{st.st_size}"

    def _state_path(self) -> Path:
        return self._rsi_dir.joinpath(*_STATE_REL)

    def _load(self) -> Dict[str, Any]:
        path = self._state_path()
        if not path.is_file():
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            logger.warning("loop-guard 状态损坏，按无状态处理: %s", path)
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, task: str, *, idle_rounds: int, last_signal: str, slowed: bool) -> None:
        data = self._load()
        entry: Dict[str, Any] = {
            "idle_rounds": idle_rounds,
            "last_signal": last_signal,
            "slowed": slowed,
        }
        if slowed:
            prev = data.get(task) or {}
            entry["slowed_at"] = prev.get("slowed_at") or self._now()
        data[task] = entry
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _write_event(self, kind: str, task: str, detail: str) -> None:
        from ..memory.logstore import append_event

        try:
            append_event(self._rsi_dir, {
                "ts": self._now(), "kind": kind, "task": task, "detail": detail,
            })
        except Exception:
            # 事件写盘失败不阻断判定本身（守卫是降耗旁路，不是安全闸）
            logger.warning("loop-guard 事件写入失败 kind=%s task=%s", kind, task, exc_info=True)

    # ---- 判定 ----

    def should_run(self, task: str) -> bool:
        """本轮是否执行。暂停中指纹变化即恢复（写 loop_guard_resumed）。"""
        state = self._load().get(task) or {}
        if not state.get("slowed"):
            return True
        current = self.signal()
        if current != str(state.get("last_signal") or ""):
            self._write_event(
                "loop_guard_resumed", task,
                f"事件流有变化，{task} 恢复正常频率",
            )
            # 事件写盘本身改变指纹，重取后再存，避免把自己的事件当成下一轮的新反馈
            self._save(task, idle_rounds=0, last_signal=self.signal(), slowed=False)
            logger.info("loop-guard：%s 检测到新事件，恢复正常频率", task)
            return True
        return False

    def record_outcome(self, task: str, *, produced: int, detail: str = "") -> bool:
        """本轮结束后记录结果；返回本轮是否被记为空转。

        有产出或事件流有变化都算进展，空转计数清零；否则计数 +1，
        达到阈值进入暂停（写 loop_guard_slowed，只写一次）。
        """
        current = self.signal()
        state = self._load().get(task) or {}
        progressed = produced > 0 or current != str(state.get("last_signal") or "")
        if progressed:
            if state.get("slowed"):
                self._write_event("loop_guard_resumed", task, "本轮有产出，恢复正常频率")
                current = self.signal()  # 事件写盘改变指纹，重取后保存
            self._save(task, idle_rounds=0, last_signal=current, slowed=False)
            return False
        idle = int(state.get("idle_rounds") or 0) + 1
        slowed = idle >= self._threshold
        if slowed and not state.get("slowed"):
            self._write_event(
                "loop_guard_slowed", task,
                f"连续 {idle} 轮无新提案且事件流无变化，{task} 降频暂停"
                + (f"（{detail}）" if detail else ""),
            )
            logger.warning("loop-guard：%s 连续 %d 轮空转，降频暂停", task, idle)
            current = self.signal()  # 同上：slowed 事件写盘后重取指纹
        self._save(task, idle_rounds=idle, last_signal=current, slowed=slowed)
        return True

    def status(self, task: Optional[str] = None) -> Dict[str, Any]:
        """观测用：返回某任务或全部任务的守卫状态。"""
        data = self._load()
        if task is not None:
            return dict(data.get(task) or {})
        return data
