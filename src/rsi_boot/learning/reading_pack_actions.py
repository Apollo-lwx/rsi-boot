"""rsi_learn pack_list / pack_open / pack_done — reading packs under .rsi/state/."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rsi_boot.scanner.reading_packs import load_index, load_pack, packs_dir, set_pack_status
from rsi_boot.ux.messages import t
from rsi_boot.ux.next import next_block

_DONE_STATUSES = frozenset({"done", "skipped"})


def _rsi_dir(store) -> Path:
    return Path(store.rsi_dir)


def _pending_ids(packs: list[dict[str, Any]]) -> list[str]:
    return [str(p["id"]) for p in packs if p.get("status") not in _DONE_STATUSES]


def _next_for_list(packs: list[dict[str, Any]], lang: str) -> dict[str, Any]:
    pending = _pending_ids(packs)
    if not packs:
        return next_block(
            [
                {"action": "bootstrap", "label": t("NEXT_BOOTSTRAP", lang)},
                {"action": "wait", "label": t("NEXT_WAIT", lang)},
            ],
            lang=lang,
        )
    if pending:
        first = pending[0]
        return next_block(
            [
                {
                    "action": "pack_open",
                    "args": {"pack_id": first},
                    "label": t("NEXT_PACK_OPEN", lang, id=first),
                },
                {"action": "pack_list", "label": t("NEXT_PACK_LIST", lang)},
                {"action": "wait", "label": t("NEXT_WAIT", lang)},
            ],
            lang=lang,
        )
    return next_block(
        [
            {"action": "knowledge_review", "label": t("NEXT_REVIEW", lang)},
            {"action": "pack_list", "label": t("NEXT_PACK_LIST", lang)},
        ],
        lang=lang,
    )


def _next_for_open(pack_id: str, lang: str) -> dict[str, Any]:
    return next_block(
        [
            {
                "action": "pack_done",
                "args": {"pack_id": pack_id, "status": "done"},
                "label": t("NEXT_PACK_DONE", lang),
            },
            {
                "action": "pack_done",
                "args": {"pack_id": pack_id, "status": "skipped"},
                "label": t("NEXT_PACK_SKIP", lang),
            },
            {"action": "pack_list", "label": t("NEXT_PACK_LIST", lang)},
        ],
        lang=lang,
    )


def _next_after_done(store, lang: str) -> dict[str, Any]:
    packs = [
        item
        for item in (load_index(_rsi_dir(store)).get("packs") or [])
        if isinstance(item, dict) and item.get("id")
    ]
    pending = [
        str(item["id"])
        for item in packs
        if item.get("status") not in _DONE_STATUSES
    ]
    if pending:
        first = pending[0]
        return next_block(
            [
                {
                    "action": "pack_open",
                    "args": {"pack_id": first},
                    "label": t("NEXT_PACK_OPEN", lang, id=first),
                },
                {"action": "pack_list", "label": t("NEXT_PACK_LIST", lang)},
            ],
            lang=lang,
        )
    return next_block(
        [
            {"action": "knowledge_review", "label": t("NEXT_REVIEW", lang)},
            {"action": "pack_list", "label": t("NEXT_PACK_LIST", lang)},
        ],
        lang=lang,
    )


def pack_list(store, lang: str = "zh") -> dict[str, Any]:
    root = packs_dir(_rsi_dir(store))
    if not root.is_dir():
        empty = {
            "total": 0,
            "done": 0,
            "pending": 0,
            "skipped": 0,
            "packs": [],
        }
        return {
            "status": "success",
            "message": t("PACK_NEED_BOOTSTRAP", lang),
            "data": empty,
            "next": _next_for_list([], lang),
        }
    packs: list[dict[str, Any]] = []
    for item in load_index(_rsi_dir(store)).get("packs") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        packs.append({
            "id": item["id"],
            "title": item.get("title", ""),
            "domain": item.get("domain", ""),
            "status": item.get("status", "pending"),
            "source_count": int(item.get("source_count") or 0),
        })
    done = sum(1 for p in packs if p["status"] == "done")
    skipped = sum(1 for p in packs if p["status"] == "skipped")
    pending = sum(1 for p in packs if p["status"] not in _DONE_STATUSES)
    return {
        "status": "success",
        "data": {
            "total": len(packs),
            "done": done,
            "pending": pending,
            "skipped": skipped,
            "packs": packs,
        },
        "next": _next_for_list(packs, lang),
    }


def pack_open(store, pack_id: str, lang: str = "zh") -> dict[str, Any]:
    pack_id = str(pack_id or "").strip()
    if not pack_id:
        return {"status": "error", "code": "invalid", "message": t("PACK_NEED_ID", lang)}
    pack = load_pack(_rsi_dir(store), pack_id)
    if pack is None:
        return {"status": "error", "code": "not_found", "message": t("PACK_NOT_FOUND", lang)}
    return {"status": "success", "data": pack, "next": _next_for_open(pack_id, lang)}


def pack_done(
    store,
    pack_id: str,
    status: str = "done",
    reason: str = "",
    lang: str = "zh",
) -> dict[str, Any]:
    pack_id = str(pack_id or "").strip()
    if not pack_id:
        return {"status": "error", "code": "invalid", "message": t("PACK_NEED_ID", lang)}
    status = str(status or "done").strip() or "done"
    if status not in _DONE_STATUSES:
        return {"status": "error", "code": "invalid", "message": t("PACK_BAD_STATUS", lang)}
    result = set_pack_status(_rsi_dir(store), pack_id, status, reason)
    if result.get("code") == "not_found":
        return {
            "status": "error",
            "code": "not_found",
            "message": t("PACK_NOT_FOUND", lang),
        }
    if result.get("status") == "success":
        result = dict(result)
        result["next"] = _next_after_done(store, lang)
    return result
