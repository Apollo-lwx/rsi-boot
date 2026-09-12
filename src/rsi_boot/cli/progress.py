"""CLI 进度：阶段、计数、剩余时间。走 stdout，不依赖 --verbose。"""

from __future__ import annotations

import math
import shutil
import sys
import time
import unicodedata
from typing import Optional, TextIO


def format_duration(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}秒"
    minutes, rem = divmod(s, 60)
    if minutes < 60:
        return f"{minutes}分" if rem == 0 else f"{minutes}分{rem}秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}小时{minutes}分"


def format_remaining(seconds: float) -> str:
    """剩余时间：有活未干完时不显示 0秒（否则像卡住）。"""
    if seconds < 1:
        return "不到1秒"
    return format_duration(math.ceil(seconds))


def display_width(text: str) -> int:
    """终端列宽：汉字全角算 2，避免 \\r 刷新按字符数估宽而换行重叠。"""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
    return width


def terminal_columns(stream: Optional[TextIO] = None) -> int:
    try:
        return max(16, shutil.get_terminal_size().columns)
    except OSError:
        return 80


def _clip(text: str, limit: int) -> str:
    if display_width(text) <= limit:
        return text
    out: list[str] = []
    used = 0
    ellipsis = "..."
    room = max(0, limit - 3)
    for ch in text:
        step = 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
        if used + step > room:
            break
        out.append(ch)
        used += step
    return "".join(out) + ellipsis


def format_bar(done: int, total: int, width: int = 20) -> str:
    """`[=====               ] 25%` — ASCII，PowerShell 也能显示。"""
    if total <= 0:
        pct = 0
    else:
        pct = min(100, max(0, int(done * 100 / total)))
    filled = width * pct // 100
    return f"[{'=' * filled}{' ' * (width - filled)}] {pct}%"


def estimate_remaining(done: int, total: int, elapsed: float) -> Optional[float]:
    if done <= 0 or total <= done or elapsed <= 0:
        return None
    return elapsed / done * (total - done)


class Progress:
    """终端进度。TTY 用 \\r 原地刷新；管道/测试打完整行。"""

    def __init__(self, stream: Optional[TextIO] = None, min_interval: float = 0.4) -> None:
        self.stream = sys.stdout if stream is None else stream
        self.min_interval = min_interval
        self.total_phases = 0
        self.phase_index = 0
        self.phase_name = ""
        self.phase_total: Optional[int] = None
        self.phase_done = 0
        self._t0 = time.monotonic()
        self._phase_t0 = self._t0
        self._last_render = 0.0
        self._last_len = 0
        self._elapsed_override: Optional[float] = None

    def start(self, total_phases: int, title: str = "") -> None:
        self.total_phases = total_phases
        self.phase_index = 0
        self.phase_name = ""
        self._t0 = time.monotonic()
        self._phase_t0 = self._t0
        if title:
            self._writeln(title)

    def phase(self, name: str, total: Optional[int] = None) -> None:
        if self.phase_name:
            self._complete_phase()
        self.phase_index += 1
        self.phase_name = name
        self.phase_total = total
        self.phase_done = 0
        self._phase_t0 = time.monotonic()
        self._last_render = 0.0
        self._elapsed_override = None
        self._render(force=True)

    def retarget(self, name: str, total: Optional[int] = None) -> None:
        """同一阶段换子步骤（不占新序号），避免配对时停在 100% 像卡住。"""
        self.phase_name = name
        self.phase_total = total
        self.phase_done = 0
        self._phase_t0 = time.monotonic()
        self._elapsed_override = None
        self._last_render = 0.0
        self._render(force=True)

    def tick(self, done: Optional[int] = None, increment: int = 1) -> None:
        if done is not None:
            self.phase_done = done
        else:
            self.phase_done += increment
        self._render()

    def writeln(self, text: str) -> None:
        """另起一行写说明，避免打断 TTY 上的 \\r 进度。"""
        self._writeln(text)

    def finish(self, message: str = "") -> None:
        if self.phase_name:
            self._complete_phase()
        extra = f"  {message}" if message else ""
        self._writeln(f"学习完成，总耗时 {format_duration(self._total_elapsed())}{extra}")

    def _phase_elapsed(self) -> float:
        if self._elapsed_override is not None:
            return self._elapsed_override
        return time.monotonic() - self._phase_t0

    def _total_elapsed(self) -> float:
        return time.monotonic() - self._t0

    def _complete_phase(self) -> None:
        if self.phase_total is not None:
            self.phase_done = max(self.phase_done, self.phase_total)
        self._render(force=True, done=True)
        self._break_cr()

    def _isatty(self) -> bool:
        return bool(getattr(self.stream, "isatty", lambda: False)())

    def _render(self, force: bool = False, done: bool = False) -> None:
        now = time.monotonic()
        if not force and not done and (now - self._last_render) < self.min_interval:
            return
        self._last_render = now
        line = self._format_line(done=done, fit=self._isatty())
        if self._isatty() and not done:
            cols = terminal_columns(self.stream)
            line = self._fit(line, cols - 1)
            pad = max(0, self._last_len - display_width(line))
            self.stream.write("\r" + line + " " * pad)
            self.stream.flush()
            self._last_len = display_width(line)
            return
        self._writeln(line)

    def _break_cr(self) -> None:
        if self._isatty() and self._last_len:
            self.stream.write("\n")
            self.stream.flush()
            self._last_len = 0

    def _writeln(self, text: str) -> None:
        self._break_cr()
        self.stream.write(text + "\n")
        self.stream.flush()

    def _fit(self, line: str, limit: int) -> str:
        if display_width(line) <= limit:
            return line
        return _clip(line, limit)

    def _format_line(self, done: bool = False, *, fit: bool = False) -> str:
        prefix = f"[{self.phase_index}/{self.total_phases}] {self.phase_name}"
        bar = ""
        count = ""
        if self.phase_total:
            shown = self.phase_total if done else self.phase_done
            bar = format_bar(shown, self.phase_total)
            count = f"{shown}/{self.phase_total}"
        elif self.phase_done:
            count = str(self.phase_done)
        elapsed = self._phase_elapsed()
        elapsed_s = f"已用 {format_duration(elapsed)}"
        remain_s = ""
        if not done and self.phase_total:
            remain = estimate_remaining(self.phase_done, self.phase_total, elapsed)
            if remain is not None:
                remain_s = f"约剩 {format_remaining(remain)}"
        full = [prefix, bar, count, elapsed_s, remain_s]
        if not fit:
            return "  ".join(part for part in full if part)
        limit = max(terminal_columns(self.stream) - 1, 16)
        for candidate in (
            full,
            [prefix, count, elapsed_s, remain_s],
            [prefix, count, elapsed_s],
            [prefix, count],
            [prefix],
        ):
            line = "  ".join(part for part in candidate if part)
            if display_width(line) <= limit:
                return line
        return _clip(prefix, limit)
