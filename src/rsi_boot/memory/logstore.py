"""Append-only JSONL event log; retrieved is document ids."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_DOC_ID = re.compile(r"^[0-9a-f]{32}$")
_ROLLED = re.compile(r"^events-(\d{6})\.jsonl$")
ROLL_THRESHOLD_BYTES = 50 * 1024 * 1024
_EVENTS_NAME = "events.jsonl"
_ERRORS_NAME = "errors.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _logs_dir(rsi_dir: Path) -> Path:
    return Path(rsi_dir) / "logs"


def _validate_retrieved(event: dict) -> None:
    if "retrieved" not in event:
        return
    retrieved = event["retrieved"]
    if retrieved is None:
        return
    if not isinstance(retrieved, list) or not all(
        isinstance(item, str) and _DOC_ID.fullmatch(item) for item in retrieved
    ):
        raise ValueError("retrieved must be a list of 32-hex document ids")


def _append_copy(src: Path, dest: Path, *, chunk_size: int = 1024 * 1024) -> None:
    with dest.open("ab") as out, src.open("rb") as inp:
        while True:
            chunk = inp.read(chunk_size)
            if not chunk:
                break
            out.write(chunk)
        out.flush()


def _roll_if_needed(events_path: Path) -> None:
    if not events_path.is_file() or events_path.stat().st_size < ROLL_THRESHOLD_BYTES:
        return
    dest = events_path.parent / f"events-{datetime.now(timezone.utc).strftime('%Y%m')}.jsonl"
    if dest.exists():
        _append_copy(events_path, dest)
        events_path.unlink()
    else:
        events_path.replace(dest)


def append_event(rsi_dir: Path, event: dict) -> None:
    """校验 retrieved 若存在则每项为 32 hex；写 logs/events.jsonl。
    当前文件超 50MB 则滚到 events-YYYYMM.jsonl 再写新行。"""
    _validate_retrieved(event)
    logs = _logs_dir(rsi_dir)
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / _EVENTS_NAME
    _roll_if_needed(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        fh.flush()


def _newest_rolled(logs: Path) -> Path | None:
    if not logs.is_dir():
        return None
    rolled: list[tuple[str, Path]] = []
    for path in logs.iterdir():
        match = _ROLLED.match(path.name)
        if match:
            rolled.append((match.group(1), path))
    if not rolled:
        return None
    rolled.sort(key=lambda item: item[0])
    return rolled[-1][1]


def _log_bad_line(errors: Path, path: Path, line: str, exc: BaseException) -> None:
    errors.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": _utc_now(),
        "kind": "jsonl_invalid",
        "path": str(path),
        "line": line[:500],
        "error": str(exc),
    }
    with errors.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _iter_file(
    path: Path, *, kinds: set[str] | None, errors: Path
) -> Iterator[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            _log_bad_line(errors, path, line, exc)
            continue
        if not isinstance(obj, dict):
            _log_bad_line(errors, path, line, ValueError("event must be an object"))
            continue
        if kinds is not None and obj.get("kind") not in kinds:
            continue
        yield obj


def iter_events(rsi_dir: Path, *, kinds: set[str] | None = None) -> Iterator[dict]:
    """扫 events.jsonl 与最近一个 events-YYYYMM.jsonl；坏行跳过并记 errors.jsonl。"""
    logs = _logs_dir(rsi_dir)
    errors = logs / _ERRORS_NAME
    rolled = _newest_rolled(logs)
    paths = []
    if rolled is not None:
        paths.append(rolled)
    current = logs / _EVENTS_NAME
    if current not in paths:
        paths.append(current)
    for path in paths:
        if path.is_file():
            yield from _iter_file(path, kinds=kinds, errors=errors)
