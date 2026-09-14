"""rsi_learn extract: classified items → patterns / gene-map/cases / teaching-cases."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.ux.messages import t

_UNSAFE_SLUG = re.compile(r"[^0-9A-Za-z._-]+")

_KIND_TYPE = {
    "pattern": "pattern",
    "gene": "gene_case",
    "teaching": "teaching_case",
    "teaching_pending": "teaching_case",
}
_KIND_SUB = {
    "pattern": "",
    "gene": "cases",
    "teaching": "",
    "teaching_pending": "pending",
}


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_slug(value: str, fallback: str = "scope") -> str:
    text = str(value).replace("\\", "/").strip()
    parts = [p for p in text.split("/") if p and p not in {".", ".."}]
    slug = _UNSAFE_SLUG.sub("-", "-".join(parts)).strip(".-")
    return slug or fallback


def _nested_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    raw = payload.get(key)
    return raw if isinstance(raw, dict) else {}


def _field_from_payload(payload: dict[str, Any], key: str) -> str:
    trigger = _nested_dict(payload, "trigger")
    lesson = _nested_dict(payload, "lesson")
    for src in (payload, trigger, lesson):
        if key in src and src[key] is not None:
            return str(src[key])
    return ""


def _sig_pair(payload: dict[str, Any]) -> tuple[str, str]:
    return (
        _field_from_payload(payload, "error_signature"),
        _field_from_payload(payload, "failure_type"),
    )


def find_duplicate(store: MemoryStore, error_signature: str, failure_type: str) -> MemoryDoc | None:
    if not error_signature:
        return None
    for typ in ("gene_case", "teaching_case", "pattern"):
        for doc in store.list_official(typ):
            sig, ft = _sig_pair(doc.payload or {})
            if sig == error_signature and ft == failure_type:
                return doc
    return None


def _dest_for(store: MemoryStore, kind: str, title: str, doc_id: str) -> Path:
    typ = _KIND_TYPE[kind]
    root = official_dir(store.rsi_dir, typ)
    sub = _KIND_SUB[kind]
    folder = root / sub if sub else root
    return folder / memory_filename(title, doc_id)


def _gene_payload(item: dict[str, Any], content: str) -> dict[str, Any]:
    raw = dict(item.get("payload") or {})
    sig, ft = _sig_pair(raw)
    return {
        "weight": raw.get("weight", 1),
        "source": raw.get("source") or "automatic",
        "scenario_type": raw.get("scenario_type") or "",
        "failure_type": ft,
        "error_signature": sig,
        "fingerprint": raw.get("fingerprint") or "",
        "solution_type": raw.get("solution_type") or "rule",
        "solution": raw.get("solution") or content,
        "validation": raw.get("validation") or "skipped",
        "scope": raw.get("scope") or "project",
    }


def extract_items(store: MemoryStore, arguments: dict[str, Any], lang: str) -> dict[str, Any]:
    items = arguments.get("items")
    if not isinstance(items, list):
        payload = arguments.get("payload") or {}
        items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return {"status": "error", "code": "invalid", "message": t("EXTRACT_NEED_ITEMS", lang)}

    scope = _safe_slug(str(arguments.get("scope") or "session").strip() or "session", fallback="session")
    closeout: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if kind == "skip_dup":
            cited = str(item.get("id") or item.get("cite") or "")
            closeout.append({
                "kind": "skip_dup",
                "id": cited,
                "path": str(item.get("path") or ""),
                "one_liner": str(item.get("one_liner") or cited),
            })
            continue
        if kind not in _KIND_TYPE:
            continue
        payload = dict(item.get("payload") or {})
        sig, ft = _sig_pair(payload)
        dup = find_duplicate(store, sig, ft)
        if dup is not None:
            closeout.append({
                "kind": "skip_dup",
                "id": dup.id,
                "path": dup.path or "",
                "one_liner": dup.title,
            })
            continue
        title = str(item.get("title") or item.get("one_liner") or kind)[:120]
        content = str(item.get("content") or item.get("one_liner") or title)[:20000]
        doc_id = uuid.uuid4().hex
        extra_payload = _gene_payload(item, content) if kind == "gene" else dict(payload)
        extra_payload["error_signature"] = sig
        extra_payload["failure_type"] = ft
        doc = MemoryDoc(
            id=doc_id,
            type=_KIND_TYPE[kind],
            title=title,
            content=content,
            source="extract",
            payload=extra_payload,
        )
        written = store.write(doc, dest=_dest_for(store, kind, title, doc_id))
        closeout.append({
            "kind": kind,
            "id": written.id,
            "path": written.path or "",
            "one_liner": str(item.get("one_liner") or written.title),
        })

    ts = _utc_ts()
    manifest_rel = f"audit/learning-extractions-{scope}-{ts}.yaml"
    dest = store.rsi_dir / manifest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        yaml.safe_dump(
            {"scope": scope, "closeout": closeout},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return {
        "status": "success",
        "message": t("EXTRACT_CLOSEOUT", lang),
        "data": {"closeout": closeout, "path": manifest_rel, "scope": scope},
    }
