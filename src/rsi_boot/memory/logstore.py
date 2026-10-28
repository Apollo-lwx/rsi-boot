"""Append-only JSONL event log; retrieved is document ids."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
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


def _rolled_files(logs: Path) -> list[Path]:
    if not logs.is_dir():
        return []
    rolled: list[tuple[str, Path]] = []
    for path in logs.iterdir():
        match = _ROLLED.match(path.name)
        if match:
            rolled.append((match.group(1), path))
    rolled.sort(key=lambda item: item[0])
    return [path for _, path in rolled]


def _newest_rolled(logs: Path) -> Path | None:
    files = _rolled_files(logs)
    return files[-1] if files else None


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


def latest_feedback_by_token(rsi_dir: Path, *, all_rolls: bool = True) -> dict[str, dict]:
    """按 token 保留最后一条反馈。文件日志把动作写在独立的 feedback 行上。"""
    latest: dict[str, dict] = {}
    for event in iter_events(rsi_dir, kinds={"feedback"}, all_rolls=all_rolls):
        token = event.get("token")
        if token:
            latest[str(token)] = event
    return latest


def event_action(event: dict, feedback_by_token: dict[str, dict] | None = None) -> str:
    """动作优先取本行；本行没有时用同一 token 的反馈行。"""
    action = event.get("action") or event.get("feedback_action")
    if action:
        return str(action)
    if not feedback_by_token:
        return ""
    token = event.get("token")
    fb = feedback_by_token.get(str(token)) if token else None
    if not fb:
        return ""
    return str(fb.get("action") or fb.get("feedback_action") or "")


def iter_events(
    rsi_dir: Path, *, kinds: set[str] | None = None, all_rolls: bool = False,
) -> Iterator[dict]:
    """扫 events.jsonl 与滚动文件。默认只读最近一个 events-YYYYMM.jsonl。

    all_rolls=True 时读全部月份滚动文件，给观察期、采纳率和回放这类跨月窗口用。
    热路径保持默认，避免每次召回扫完整历史。坏行跳过并记 errors.jsonl。
    """
    logs = _logs_dir(rsi_dir)
    errors = logs / _ERRORS_NAME
    paths = _rolled_files(logs) if all_rolls else []
    if not all_rolls:
        rolled = _newest_rolled(logs)
        if rolled is not None:
            paths.append(rolled)
    current = logs / _EVENTS_NAME
    if current not in paths:
        paths.append(current)
    for path in paths:
        if path.is_file():
            yield from _iter_file(path, kinds=kinds, errors=errors)


def _event_time(event: dict) -> datetime | None:
    text = str(event.get("ts") or event.get("created_at") or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def archive_expired_events(
    rsi_dir: Path, archive_dir: Path, retention_days: int = 90,
) -> int:
    """超过保留期的事件先追加到 archive/YYYYMM.jsonl，再从日志文件删掉。

    无时间戳的行保留。扫描全部 events.jsonl 与 events-YYYYMM.jsonl。
    读完后文件变长则本轮跳过该文件，避免和正在追加的行互相覆盖。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    logs = _logs_dir(rsi_dir)
    if not logs.is_dir():
        return 0
    sources = [
        path for path in logs.iterdir()
        if path.is_file() and (path.name == _EVENTS_NAME or _ROLLED.match(path.name))
    ]
    archived = 0
    by_month: dict[str, list[str]] = {}
    rewrites: list[tuple[Path, str]] = []
    for path in sources:
        try:
            size = path.stat().st_size
            text = path.read_text(encoding="utf-8")
            if path.stat().st_size != size:
                continue
        except OSError:
            continue
        keep: list[str] = []
        moved = False
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                keep.append(line)
                continue
            if not isinstance(obj, dict):
                keep.append(line)
                continue
            ts = _event_time(obj)
            if ts is None or ts >= cutoff:
                keep.append(line)
                continue
            by_month.setdefault(ts.strftime("%Y%m"), []).append(line)
            archived += 1
            moved = True
        if moved:
            body = ("\n".join(keep) + "\n") if keep else ""
            rewrites.append((path, body))
    if not archived:
        return 0
    archive_dir.mkdir(parents=True, exist_ok=True)
    for month, lines in by_month.items():
        dest = archive_dir / f"{month}.jsonl"
        with dest.open("a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
            fh.flush()
    for path, body in rewrites:
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
    return archived
