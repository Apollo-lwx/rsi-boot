"""Teach catch / record: drafts in state/, teaching_case + gene-map/manual pair."""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from rsi_boot.core.masking import mask_text
from rsi_boot.learning.gene_map import find_duplicate
from rsi_boot.memory.logstore import append_event, iter_events
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.ux.lang import detect_lang
from rsi_boot.ux.messages import t

_DRAFT_ID = re.compile(r"^[0-9a-f]{32}$")

RUBRIC_TEXT = """kind → write path
pattern → patterns/
gene → gene-map/cases/
teaching → teaching-cases/
low_confidence → official teaching-cases/ plus lesson.low_confidence
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


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _count_field(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if payload.get(key) is None:
            continue
        return _as_int(payload.get(key), 0)
    return None


def _load_pair(store: MemoryStore, doc_id: str) -> tuple[MemoryDoc | None, MemoryDoc | None]:
    teaching = next((d for d in store.list_official("teaching_case") if d.id == doc_id), None)
    gene = next((d for d in store.list_official("gene_case") if d.id == doc_id), None)
    return teaching, gene


def _dest_for(store: MemoryStore, doc: MemoryDoc | None, typ: str, title: str, doc_id: str) -> Path:
    if doc and doc.path:
        path = Path(doc.path)
        return path if path.is_absolute() else store.rsi_dir / doc.path
    root = official_dir(store.rsi_dir, typ)
    if typ == "gene_case":
        return root / "manual" / memory_filename(title, doc_id)
    return root / memory_filename(title, doc_id)


def _recall_hits_since(store: MemoryStore, doc_id: str, recorded_at: str) -> int:
    hits = 0
    for event in iter_events(store.rsi_dir, kinds={"recall"}):
        ts = str(event.get("ts") or "")
        if recorded_at and ts <= recorded_at:
            continue
        retrieved = event.get("retrieved") or []
        if doc_id in retrieved:
            hits += 1
    return hits


def teach_record(store: MemoryStore, arguments: dict[str, Any], lang: str) -> dict[str, Any]:
    lesson = _lesson(arguments)
    correct_fix = str(lesson.get("correct_fix") or "").strip()
    if not correct_fix:
        return {"status": "error", "code": "invalid", "message": t("TEACH_NEED_FIX", lang)}
    wrong_action = str(lesson.get("wrong_action") or "").strip()
    if not wrong_action:
        return {"status": "error", "code": "invalid", "message": t("TEACH_NEED_WRONG", lang)}
    error_signature = str(lesson.get("error_signature") or "").strip()
    if not error_signature:
        return {"status": "error", "code": "invalid", "message": t("TEACH_NEED_SIGNATURE", lang)}

    failure_type = str(lesson.get("failure_type") or "").strip()
    dup = find_duplicate(store, error_signature, failure_type)
    existing_id = (
        dup.id if dup is not None and dup.type in {"gene_case", "teaching_case"} else None
    )
    prior_teaching, prior_gene = _load_pair(store, existing_id) if existing_id else (None, None)
    doc_id = existing_id or _record_id(arguments)
    if existing_id is None:
        prior_teaching, prior_gene = _load_pair(store, doc_id)

    title = _title(lesson, correct_fix)
    author = str(lesson.get("author") or arguments.get("author") or "agent")
    low_conf = _low_confidence(arguments, lesson)
    promote = _promote_to_pattern(arguments, lesson)
    trigger = {
        "error_signature": error_signature,
        "failure_type": failure_type,
        "fingerprint": str(lesson.get("fingerprint") or ""),
    }
    attempts = arguments.get("system_attempts")
    if not isinstance(attempts, list):
        attempts = []
    if prior_teaching is not None:
        prior_attempts = list((prior_teaching.payload or {}).get("system_attempts") or [])
        attempts = prior_attempts + attempts

    prior_payload = (prior_teaching.payload if prior_teaching else None) or (prior_gene.payload if prior_gene else None) or {}
    prior_fix = prior_payload.get("fix_and_learn") if isinstance(prior_payload.get("fix_and_learn"), dict) else {}
    recorded_at = str(prior_fix.get("recorded_at") or prior_payload.get("recorded_at") or "")
    record_count = _count_field(prior_fix, "record_count", "hit_count")
    if record_count is None:
        record_count = _count_field(prior_payload, "record_count", "hit_count")
    recall_hits = _count_field(prior_fix, "recall_hits")
    if recall_hits is None:
        recall_hits = _count_field(prior_payload, "recall_hits") or 0
    weight = _count_field(prior_payload, "weight")
    if weight is None:
        weight = _count_field(prior_fix, "gene_map_weight")

    if prior_teaching is None and prior_gene is None:
        record_count = 1
        recall_hits = 0
        weight = 10 if author == "user" else 2
    else:
        record_count = (record_count or 1) + 1
        new_hits = _recall_hits_since(store, doc_id, recorded_at)
        if new_hits:
            weight = min(10, (weight if weight is not None else 2) + 2)
            recall_hits = (recall_hits or 0) + new_hits
        elif weight is None:
            weight = 2
    if author == "user":
        weight = max(weight or 0, 10)
    recorded_at = _utc_stamp()

    lesson_payload = {
        "author": author,
        "correct_fix": correct_fix,
        "reason": str(lesson.get("reason") or ""),
        "applies_to": list(lesson.get("applies_to") or []),
        "tags": list(lesson.get("tags") or []),
        "open_questions": list(lesson.get("open_questions") or []),
        "wrong_action": wrong_action,
    }
    if low_conf:
        lesson_payload["low_confidence"] = True

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
            "lesson": lesson_payload,
            "fix_and_learn": {
                "applied": bool((arguments.get("fix_and_learn") or {}).get("applied", False)),
                "validation": "",
                "gene_map_weight": weight,
                "promote_to_pattern": promote,
                "record_count": record_count,
                "recall_hits": recall_hits,
                "recorded_at": recorded_at,
            },
        },
    )
    teaching_dest = _dest_for(store, prior_teaching, "teaching_case", title, doc_id)
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
            "record_count": record_count,
            "recall_hits": recall_hits,
            "recorded_at": recorded_at,
        },
    )
    gene_dest = _dest_for(store, prior_gene, "gene_case", title, doc_id)
    gene_written = store.write(gene, dest=gene_dest)

    closeout: list[dict[str, Any]] = [
        {"kind": "teaching", "path": written.path, "one_liner": written.title},
        {"kind": "gene", "path": gene_written.path, "one_liner": gene_written.title},
    ]
    if promote:
        pattern_id = uuid.uuid4().hex
        pattern = MemoryDoc(
            id=pattern_id,
            type="pattern",
            title=title[:120],
            content=correct_fix[:20000],
            source="manual",
            refs=[doc_id],
            payload={"source": "teach_record"},
        )
        pat_dest = official_dir(store.rsi_dir, "pattern") / memory_filename(title, pattern_id)
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


def _safe_draft_path(rsi_dir: Path, draft_id: str) -> Path | None:
    val = str(draft_id or "").strip().lower()
    if not _DRAFT_ID.fullmatch(val):
        return None
    root = drafts_dir(rsi_dir).resolve()
    path = (root / f"{val}.yaml").resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def skip_draft(store: MemoryStore, arguments: dict[str, Any], lang: str) -> dict[str, Any]:
    draft_id = str(arguments.get("id") or arguments.get("draft_id") or "")
    path = _safe_draft_path(store.rsi_dir, draft_id)
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
        path = _safe_draft_path(rsi_dir, str(cid))
        if path is not None and path.is_file():
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
