"""Watch memory/**/*.yaml only; debounce ≥300ms for the live loop."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Callable

DEBOUNCE_MS = 300


class YamlWatcher:
    DEBOUNCE_MS = DEBOUNCE_MS

    def __init__(
        self,
        root: Path,
        on_change: Callable[[Path], None],
        debounce_ms: int = DEBOUNCE_MS,
    ):
        self.root = Path(root)
        self.on_change = on_change
        self.debounce_ms = max(int(debounce_ms), DEBOUNCE_MS)
        self._mtimes: dict[Path, float] = {}
        self._seed()

    def _seed(self) -> None:
        for path in self._yaml_paths():
            try:
                self._mtimes[path] = path.stat().st_mtime
            except OSError:
                continue

    def _yaml_paths(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(
            p for p in self.root.rglob("*.yaml")
            if p.is_file() and not p.name.endswith(".tmp")
        )

    def _changed(self) -> list[Path]:
        current: dict[Path, float] = {}
        changed: list[Path] = []
        for path in self._yaml_paths():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            current[path] = mtime
            prev = self._mtimes.get(path)
            if prev is None or prev != mtime:
                changed.append(path)
        gone = [p for p in self._mtimes if p not in current]
        self._mtimes = current
        return changed + gone

    async def poll_once(self) -> list[Path]:
        """Scan once and fire immediately (tests). Live loop applies debounce."""
        changed = self._changed()
        for path in changed:
            self.on_change(path)
        return changed

    async def watch_loop(self, interval_s: float = 0.2) -> None:
        pending: dict[Path, float] = {}
        while True:
            now = time.monotonic()
            for path in self._changed():
                pending[path] = now
            ready = [
                path for path, seen_at in list(pending.items())
                if (now - seen_at) * 1000 >= self.debounce_ms
            ]
            for path in ready:
                pending.pop(path, None)
                self.on_change(path)
            await asyncio.sleep(interval_s)
