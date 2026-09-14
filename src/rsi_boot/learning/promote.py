"""Promote pattern/teaching successes into pending norms. Never writes patterns/ or injects."""

from __future__ import annotations

import uuid
from typing import Any

from rsi_boot.learning.gene_map import _sig_pair
from rsi_boot.memory.graph import add_edge, load_catalog
from rsi_boot.memory.logstore import iter_events
from rsi_boot.memory.paths import pending_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.ux.messages import t

_SUCCESS_ACTIONS = frozenset({"accepted", "applied"})
_NORM_TYPES = frozenset({"prohibition", "convention", "skill"})


def _applies_to(doc: MemoryDoc) -> list[str]:
    payload = doc.payload or {}
    lesson = payload.get("lesson") if isinstance(payload.get("lesson"), dict) else {}
    raw = lesson.get("applies_to") or payload.get("applies_to") or []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def _promote_flagged(doc: MemoryDoc) -> bool:
    payload = doc.payload or {}
    if payload.get("promote_to_pattern"):
        return True
    fix = payload.get("fix_and_learn")
    return isinstance(fix, dict) and bool(fix.get("promote_to_pattern"))


def _validation_passed(doc: MemoryDoc) -> bool:
    payload = doc.payload or {}
    if payload.get("validation") == "passed":
        return True
    fix = payload.get("fix_and_learn")
    return isinstance(fix, dict) and fix.get("validation") == "passed"


def _norm_type(doc: MemoryDoc) -> str:
    payload = doc.payload or {}
    explicit = payload.get("norm_type") or payload.get("promote_as")
    if explicit in _NORM_TYPES:
        return str(explicit)
    if payload.get("name"):
        return "skill"
    if (doc.title or "").startswith("禁止"):
        return "prohibition"
    return "convention"


def _domain_scope(arguments: dict[str, Any], doc: MemoryDoc) -> bool:
    if arguments.get("scope") == "domain":
        return True
    return (doc.payload or {}).get("scope") == "domain"


def _already_distilled(rsi_dir, source_id: str) -> bool:
    for edge in load_catalog(rsi_dir).get("edges") or []:
        if edge.get("rel") == "distilled_from" and edge.get("to") == source_id:
            return True
    return False


def _event_pair(event: dict[str, Any]) -> tuple[str, str]:
    pair = _sig_pair(event)
    if pair[0]:
        return pair
    payload = event.get("payload")
    if isinstance(payload, dict):
        return _sig_pair(payload)
    return ("", "")


def _success_counts(store: MemoryStore) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}

    def _bump(pair: tuple[str, str]) -> None:
        if pair[0]:
            counts[pair] = counts.get(pair, 0) + 1

    for event in iter_events(store.rsi_dir):
        action = event.get("action") or event.get("feedback_action")
        if action not in _SUCCESS_ACTIONS:
            continue
        pair = _event_pair(event)
        if pair[0]:
            _bump(pair)
            continue
        for rid in event.get("retrieved") or []:
            if not isinstance(rid, str):
                continue
            try:
                doc = store.read(rid)
            except FileNotFoundError:
                continue
            _bump(_sig_pair(doc.payload or {}))

    for typ in ("gene_case", "teaching_case", "pattern"):
        for doc in store.list_official(typ):
            if _validation_passed(doc):
                _bump(_sig_pair(doc.payload or {}))
    return counts


def _candidates(store: MemoryStore) -> list[MemoryDoc]:
    counts = _success_counts(store)
    seen: set[str] = set()
    out: list[MemoryDoc] = []
    for typ in ("pattern", "teaching_case"):
        for doc in store.list_official(typ):
            if doc.id in seen:
                continue
            pair = _sig_pair(doc.payload or {})
            flagged = doc.type == "teaching_case" and _promote_flagged(doc)
            enough = bool(pair[0]) and counts.get(pair, 0) >= 3
            if flagged or enough:
                seen.add(doc.id)
                out.append(doc)
    return out


def promote_items(store: MemoryStore, arguments: dict[str, Any], lang: str) -> dict[str, Any]:
    written: list[dict[str, Any]] = []
    for source in _candidates(store):
        if _already_distilled(store.rsi_dir, source.id):
            continue
        typ = _norm_type(source)
        if typ == "prohibition" and _domain_scope(arguments, source):
            applies = {item.lower() for item in _applies_to(source)}
            if "global" not in applies:
                continue
        doc_id = uuid.uuid4().hex
        title = source.title[:120]
        content = (source.content or source.title)[:20000]
        dest = pending_dir(store.rsi_dir, typ) / memory_filename(title, doc_id)
        pair = _sig_pair(source.payload or {})
        doc = MemoryDoc(
            id=doc_id,
            type=typ,
            title=title,
            content=content,
            source="promote",
            refs=[source.id],
            payload={
                "error_signature": pair[0],
                "failure_type": pair[1],
                "source_id": source.id,
            },
        )
        saved = store.write(doc, dest=dest)
        add_edge(store.rsi_dir, saved.id, source.id, "distilled_from")
        written.append({
            "kind": typ,
            "id": saved.id,
            "path": saved.path,
            "one_liner": saved.title,
        })
    if not written:
        return {
            "status": "success",
            "message": t("PROMOTE_NONE", lang),
            "data": {"written": [], "closeout": []},
        }
    return {
        "status": "success",
        "message": t("PROMOTE_DONE", lang, n=len(written)),
        "data": {"written": written, "closeout": written},
    }
