"""Teach catch / record: drafts in state/, teaching_case + gene-map/manual pair."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from rsi_boot.core.masking import mask_text
from rsi_boot.memory.logstore import append_event
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.ux.lang import detect_lang
from rsi_boot.ux.messages import t

RUBRIC_TEXT = """kind → write path
pattern → patterns/
gene → gene-map/cases/
teaching → teaching-cases/
teaching_pending → teaching-cases/pending/
skip_dup → do not write; cite existing id
RSI does not write the lesson. Host writes Catch→Teach→Fix then teach_record.
"""


def resolve_lang(arguments: dict[str, Any]) -> str:
    explicit = arguments.get("lang")
    if explicit in ("zh", "en"):
        return detect_lang(explicit=explicit)
    env = (os.environ.get("RSI_LANG") or "").strip().lower()
    if env in ("zh", "en"):
        return env
    chunks: list[str] = []
    for key in ("task", "title", "content", "reason", "message"):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip():
            chunks.append(val)
    lesson = arguments.get("lesson")
    if isinstance(lesson, dict):
        for val in lesson.values():
            if isinstance(val, str) and val.strip():
                chunks.append(val)
    if chunks:
        return detect_lang(*chunks)
    return detect_lang()


def drafts_dir(rsi_dir: Path) -> Path:
    return Path(rsi_dir) / "state" / "teach-drafts"


def teach_catch(store: MemoryStore, arguments: dict[str, Any], lang: str) -> dict[str, Any]:
    draft_id = uuid.uuid4().hex
    trigger = arguments.get("trigger")
    if not isinstance(trigger, dict):
        trigger = (arguments.get("payload") or {}).get("trigger") or {}
    attempts = arguments.get("system_attempts")
    if not isinstance(attempts, list):
        attempts = (arguments.get("payload") or {}).get("system_attempts") or []
    dest = drafts_dir(store.rsi_dir) / f"{draft_id}.yaml"
    text = yaml.safe_dump(
        {
            "id": draft_id,
            "trigger": _mask_value(trigger),
            "system_attempts": _mask_value(attempts),
        },
        allow_unicode=True,
        sort_keys=False,
    )
    _atomic_write(dest, text)
    rel = f"state/teach-drafts/{draft_id}.yaml"
    return {
        "status": "success",
        "message": t("TEACH_CATCH_SAVED", lang, id=draft_id),
        "data": {"id": draft_id, "path": rel},
    }


def teach_record(store: MemoryStore, arguments: dict[str, Any], lang: str) -> dict[str, Any]:
    lesson = _lesson(arguments)
    correct_fix = str(lesson.get("correct_fix") or "").strip()
    if not correct_fix:
        return {"status": "error", "code": "invalid", "message": t("TEACH_NEED_FIX", lang)}

    doc_id = _record_id(arguments)
    title = _title(lesson, correct_fix)
    author = str(lesson.get("author") or arguments.get("author") or "agent")
    weight = 10 if author == "user" else 8
    low_conf = _low_confidence(arguments, lesson)
    promote = _promote_to_pattern(arguments, lesson)
    trigger = {
        "error_signature": str(lesson.get("error_signature") or ""),
        "failure_type": str(lesson.get("failure_type") or ""),
        "fingerprint": str(lesson.get("fingerprint") or ""),
    }
    attempts = arguments.get("system_attempts")
    if not isinstance(attempts, list):
        attempts = []

    teaching = MemoryDoc(
        id=doc_id,
        type="teaching_case",
        title=title[:120],
        content=correct_fix[:20000],
        source="manual",
        payload={
            "scenario_type": str(lesson.get("scenario_type") or arguments.get("scenario_type") or "repair"),
            "trigger": trigger,
            "system_attempts": attempts,
            "lesson": {
                "author": author,
                "correct_fix": correct_fix,
                "reason": str(lesson.get("reason") or ""),
                "applies_to": list(lesson.get("applies_to") or []),
                "tags": list(lesson.get("tags") or []),
                "open_questions": list(lesson.get("open_questions") or []),
                "wrong_action": str(lesson.get("wrong_action") or ""),
            },
            "fix_and_learn": {
                "applied": bool((arguments.get("fix_and_learn") or {}).get("applied", False)),
                "validation": "",
                "gene_map_weight": weight,
                "promote_to_pattern": promote,
            },
        },
    )
    teaching_root = official_dir(store.rsi_dir, "teaching_case")
    if low_conf:
        teaching_dest = teaching_root / "pending" / memory_filename(title, doc_id)
    else:
        teaching_dest = teaching_root / memory_filename(title, doc_id)
    written = store.write(teaching, dest=teaching_dest)

    gene = MemoryDoc(
        id=doc_id,
        type="gene_case",
        title=title[:120],
        content=correct_fix[:20000],
        source="manual",
        payload={
            "weight": weight,
            "source": "manual",
            "scenario_type": teaching.payload["scenario_type"],
            "failure_type": trigger["failure_type"],
            "error_signature": trigger["error_signature"],
            "fingerprint": trigger["fingerprint"],
            "solution_type": "manual-teaching",
            "solution": correct_fix,
            "validation": "skipped",
            "scope": "project",
        },
    )
    gene_dest = official_dir(store.rsi_dir, "gene_case") / "manual" / memory_filename(title, doc_id)
    gene_written = store.write(gene, dest=gene_dest)

    closeout: list[dict[str, Any]] = [
        {"kind": "teaching", "path": written.path, "one_liner": written.title},
        {"kind": "gene", "path": gene_written.path, "one_liner": gene_written.title},
    ]
    if promote:
        pattern = MemoryDoc(
            id=doc_id,
            type="pattern",
            title=title[:120],
            content=correct_fix[:20000],
            source="manual",
            payload={"source": "teach_record"},
        )
        pat_dest = official_dir(store.rsi_dir, "pattern") / memory_filename(title, doc_id)
        pat_written = store.write(pattern, dest=pat_dest)
        closeout.append({"kind": "pattern", "path": pat_written.path, "one_liner": pat_written.title})

    _delete_matching_draft(store.rsi_dir, arguments, lesson)
    return {
        "status": "success",
        "message": t("TEACH_RECORDED", lang, path=written.path),
        "data": {
            "path": written.path,
            "id": written.id,
            "manual_path": gene_written.path,
            "closeout": closeout,
            "status": written.status,
        },
    }


def skip_draft(store: MemoryStore, arguments: dict[str, Any], lang: str) -> dict[str, Any]:
    draft_id = str(arguments.get("id") or arguments.get("draft_id") or "")
    path = drafts_dir(store.rsi_dir) / f"{draft_id}.yaml" if draft_id else None
    if path is not None and path.is_file():
        path.unlink()
    reason = str(arguments.get("reason") or arguments.get("skip_reason") or "")
    append_event(store.rsi_dir, {
        "id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "learn",
        "action": "skip",
        "draft_id": draft_id,
        "reason": reason,
        "retrieved": [],
    })
    return {
        "status": "success",
        "message": t("TEACH_SKIPPED", lang, id=draft_id),
        "data": {"id": draft_id, "closeout": [{"kind": "skip", "reason": reason}]},
    }


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(v) for v in value]
    return value


def _atomic_write(dest: Path, text: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(dest) + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, dest)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _lesson(arguments: dict[str, Any]) -> dict[str, Any]:
    lesson = arguments.get("lesson")
    if isinstance(lesson, dict) and lesson:
        out = dict(lesson)
    else:
        payload = arguments.get("payload") or {}
        nested = payload.get("lesson") if isinstance(payload, dict) else None
        out = dict(nested) if isinstance(nested, dict) else {}
    if arguments.get("correct_fix") and not out.get("correct_fix"):
        out["correct_fix"] = arguments["correct_fix"]
    for key in ("error_signature", "failure_type", "wrong_action", "author"):
        if arguments.get(key) and not out.get(key):
            out[key] = arguments[key]
    return out


def _title(lesson: dict[str, Any], correct_fix: str) -> str:
    for key in ("title", "correct_fix", "error_signature", "wrong_action"):
        val = str(lesson.get(key) or "").strip()
        if val:
            return val[:120]
    return correct_fix[:120]


def _record_id(arguments: dict[str, Any]) -> str:
    for key in ("id", "draft_id"):
        val = str(arguments.get(key) or "").strip().lower()
        if len(val) == 32 and all(c in "0123456789abcdef" for c in val):
            return val
    return uuid.uuid4().hex


def _low_confidence(arguments: dict[str, Any], lesson: dict[str, Any]) -> bool:
    if arguments.get("low_confidence") or arguments.get("pending"):
        return True
    if lesson.get("low_confidence") or lesson.get("confidence") in ("low", "pending"):
        return True
    payload = arguments.get("payload") or {}
    if isinstance(payload, dict) and (payload.get("low_confidence") or payload.get("pending")):
        return True
    hint = str(arguments.get("path") or "").replace("\\", "/")
    return "teaching-cases/pending" in hint


def _promote_to_pattern(arguments: dict[str, Any], lesson: dict[str, Any]) -> bool:
    if arguments.get("promote_to_pattern") or lesson.get("promote_to_pattern"):
        return True
    payload = arguments.get("payload") or {}
    if not isinstance(payload, dict):
        return False
    fix = payload.get("fix_and_learn") or {}
    return bool(payload.get("promote_to_pattern") or (isinstance(fix, dict) and fix.get("promote_to_pattern")))


def _delete_matching_draft(rsi_dir: Path, arguments: dict[str, Any], lesson: dict[str, Any]) -> None:
    ddir = drafts_dir(rsi_dir)
    candidates = [
        arguments.get("id"),
        arguments.get("draft_id"),
        lesson.get("draft_id"),
    ]
    for cid in candidates:
        if not cid:
            continue
        path = ddir / f"{cid}.yaml"
        if path.is_file():
            path.unlink()
            return
    sig = str(lesson.get("error_signature") or "")
    if not sig or not ddir.is_dir():
        return
    for path in ddir.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        trigger = raw.get("trigger") or {}
        if isinstance(trigger, dict) and trigger.get("error_signature") == sig:
            path.unlink()
            return
