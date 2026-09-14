"""Force UTF-8 stdio so Windows GBK consoles don't turn CJK into replacement chars."""

from __future__ import annotations

import os
import sys

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def ensure_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8. Safe to call more than once."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except (AttributeError, OSError):
            pass
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def captured_progress_ui() -> bool:
    """Cursor agent / CI / explicit plain: PTY may say tty, but the UI is a log pane."""
    if os.environ.get("RSI_PROGRESS_PLAIN", "").strip().lower() in _TRUTHY:
        return True
    if os.environ.get("CURSOR_AGENT", "").strip():
        return True
    return False
