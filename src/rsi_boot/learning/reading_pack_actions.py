"""rsi_learn pack_list / pack_open / pack_done — reading packs under .rsi/state/."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rsi_boot.scanner.reading_packs import load_index, load_pack, packs_dir, set_pack_status
from rsi_boot.ux.messages import t

_DONE_STATUSES = frozenset({"done", "skipped"})


def _rsi_dir(store) -> Path:
    return Path(store.rsi_dir)


def pack_list(store, lang: str = "zh") -> dict[str, Any]:
    root = packs_dir(_rsi_dir(store))
    if not root.is_dir():
        return {
            "status": "success",
            "message": t("PACK_NEED_BOOTSTRAP", lang),
            "data": {
                "total": 0,
                "done": 0,
                "pending": 0,
                "skipped": 0,
                "packs": [],
            },
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
    }


def pack_open(store, pack_id: str, lang: str = "zh") -> dict[str, Any]:
    pack_id = str(pack_id or "").strip()
    if not pack_id:
        return {"status": "error", "code": "invalid", "message": t("PACK_NEED_ID", lang)}
    pack = load_pack(_rsi_dir(store), pack_id)
    if pack is None:
        return {"status": "error", "code": "not_found", "message": t("PACK_NOT_FOUND", lang)}
    return {"status": "success", "data": pack}


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
    return result
