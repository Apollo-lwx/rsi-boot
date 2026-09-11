"""三态熔断器（§5.1）：纯进程内内存状态，无持久化、无跨进程共享。

失败计数口径：连续失败计数（任何一次成功立即清零，不用滑动窗口）；
HTTP 4xx（请求本身问题）不计入失败——由调用方以 countable=False 上报。
状态计时用单调时钟（time.monotonic），不受系统时间跳跃影响。

状态机：
CLOSED ──连续失败 ≥ threshold──▶ OPEN ──距 opened_at ≥ recovery_timeout──▶ HALF_OPEN
HALF_OPEN 放行 1 个探测请求（并发仅第一个放行，其余 fail-fast）：
探测成功 → CLOSED 清零；探测失败 → OPEN 重新计时。
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Dict

from ..core.exceptions import CircuitOpenError

logger = logging.getLogger(__name__)


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, name: str, threshold: int = 5, recovery_timeout_s: float = 60.0):
        self.name = name
        self.threshold = threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.state = State.CLOSED
        self.failure_count = 0
        self._opened_at: float = 0.0
        self._probe_inflight = False  # HALF_OPEN 单探测放行标记

    def allow_request(self) -> None:
        """放行检查；OPEN 且未到恢复时间 → fail-fast 抛 CircuitOpenError"""
        if self.state is State.CLOSED:
            return
        if self.state is State.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_timeout_s:
                self.state = State.HALF_OPEN
                logger.info("熔断器 %s 进入 HALF_OPEN（放行单探测）", self.name)
            else:
                raise CircuitOpenError(f"熔断器 {self.name} OPEN，请求快速拒绝")
        # HALF_OPEN：仅放行第一个探测，其余 fail-fast
        if self.state is State.HALF_OPEN:
            if self._probe_inflight:
                raise CircuitOpenError(f"熔断器 {self.name} HALF_OPEN 探测中，请求快速拒绝")
            self._probe_inflight = True

    def on_success(self) -> None:
        if self.state is not State.CLOSED:
            logger.info("熔断器 %s 探测成功，回到 CLOSED", self.name)
        self.state = State.CLOSED
        self.failure_count = 0
        self._probe_inflight = False

    def on_failure(self, countable: bool = True) -> None:
        """失败上报；countable=False（HTTP 4xx 等请求自身问题）不计数"""
        self._probe_inflight = False
        if not countable:
            return
        self.failure_count += 1
        if self.state is State.HALF_OPEN:
            self._trip()  # 探测失败：回 OPEN 并重新计时
        elif self.state is State.CLOSED and self.failure_count >= self.threshold:
            self._trip()

    def _trip(self) -> None:
        self.state = State.OPEN
        self._opened_at = time.monotonic()
        logger.warning("熔断器 %s OPEN（连续失败 %d 次，%ds 后探测恢复）",
                       self.name, self.failure_count, int(self.recovery_timeout_s))


class CircuitBreakerRegistry:
    """按依赖名管理熔断器实例（§5.2：每个依赖独立实例）"""

    def __init__(self) -> None:
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get(self, name: str, threshold: int = 5, recovery_timeout_s: float = 60.0) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name, threshold, recovery_timeout_s)
        return self._breakers[name]

    def states(self) -> Dict[str, str]:
        return {name: b.state.value for name, b in self._breakers.items()}
