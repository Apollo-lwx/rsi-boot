"""rsi_learn pack_list / pack_open / pack_done — reading packs under .rsi/state/."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rsi_boot.scanner.reading_packs import (
    LANE_IDS,
    load_index,
    load_pack,
    pack_lane,
    packs_dir,
    reopen_packs_pending,
    required_item_count,
    set_pack_status,
    skip_allowed,
    summarize_lanes,
)
from rsi_boot.scanner.report import refresh_report
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
            ],
            prompt=t("PACK_FINISH_SESSION", lang, pending=len(pending)),
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


def reopen_unallowed_skips(store, lane: str = "") -> int:
    """把不允许 skip 的 skipped 包改回 pending。返回重开数量。"""
    wanted = str(lane or "").strip()
    to_reopen = []
    for item in load_index(_rsi_dir(store)).get("packs") or []:
        if not isinstance(item, dict) or item.get("status") != "skipped":
            continue
        domain = str(item.get("domain") or "")
        if wanted and wanted in LANE_IDS and pack_lane(domain) != wanted:
            continue
        pack = load_pack(_rsi_dir(store), str(item["id"])) or item
        if skip_allowed(pack):
            continue
        to_reopen.append(str(item["id"]))
    if not to_reopen:
        return 0
    return reopen_packs_pending(_rsi_dir(store), to_reopen)


def pack_list(store, lang: str = "zh", lane: str = "", reopen_skipped: bool = False) -> dict[str, Any]:
    reopened = reopen_unallowed_skips(store, lane) if reopen_skipped else 0
    root = packs_dir(_rsi_dir(store))
    if not root.is_dir():
        empty = {
            "total": 0,
            "done": 0,
            "pending": 0,
            "skipped": 0,
            "packs": [],
            "lanes": [],
        }
        return {
            "status": "success",
            "message": t("PACK_NEED_BOOTSTRAP", lang),
            "data": empty,
            "next": _next_for_list([], lang),
        }
    wanted = str(lane or "").strip()
    packs: list[dict[str, Any]] = []
    for item in load_index(_rsi_dir(store)).get("packs") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        domain = str(item.get("domain") or "")
        row = {
            "id": item["id"],
            "title": item.get("title", ""),
            "domain": domain,
            "lane": pack_lane(domain),
            "status": item.get("status", "pending"),
            "source_count": int(item.get("source_count") or 0),
        }
        if wanted and wanted in LANE_IDS and row["lane"] != wanted:
            continue
        packs.append(row)
    done = sum(1 for p in packs if p["status"] == "done")
    skipped = sum(1 for p in packs if p["status"] == "skipped")
    pending = sum(1 for p in packs if p["status"] not in _DONE_STATUSES)
    data = {
        "total": len(packs),
        "done": done,
        "pending": pending,
        "skipped": skipped,
        "packs": packs,
        "lanes": summarize_lanes(packs),
        "reopened": reopened,
    }
    out: dict[str, Any] = {
        "status": "success",
        "data": data,
        "next": _next_for_list(packs, lang),
    }
    if pending:
        out["message"] = t("PACK_FINISH_SESSION", lang, pending=pending)
    return out


def pack_open(store, pack_id: str, lang: str = "zh") -> dict[str, Any]:
    pack_id = str(pack_id or "").strip()
    if not pack_id:
        return {"status": "error", "code": "invalid", "message": t("PACK_NEED_ID", lang)}
    pack = load_pack(_rsi_dir(store), pack_id)
    if pack is None:
        return {"status": "error", "code": "not_found", "message": t("PACK_NOT_FOUND", lang)}
    return {"status": "success", "data": pack, "next": _next_for_open(pack_id, lang)}


def _distilled_count(store, pack_id: str) -> int:
    tag = f"pack_id:{pack_id}"
    n = 0
    docs = list(store.list_pending()) + list(store.list_official(skip_harvest=True))
    for doc in docs:
        tags = set(doc.tags or [])
        if "signal:distilled" in tags and tag in tags:
            n += 1
    return n


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
    if status not in {"done", "skipped", "pending"}:
        return {"status": "error", "code": "invalid", "message": t("PACK_BAD_STATUS", lang)}
    pack = load_pack(_rsi_dir(store), pack_id)
    if pack is None:
        return {
            "status": "error",
            "code": "not_found",
            "message": t("PACK_NOT_FOUND", lang),
        }
    if status == "skipped" and pack.get("status") != "skipped":
        if not skip_allowed(pack):
            return {
                "status": "error",
                "code": "skip_not_allowed",
                "message": t("PACK_SKIP_NOT_ALLOWED", lang),
            }
    if status == "done" and pack.get("status") != "done":
        source_count = int(pack.get("source_count") or len(pack.get("sources") or []))
        need = required_item_count(str(pack.get("domain") or ""), source_count)
        have = _distilled_count(store, pack_id)
        if have < need:
            return {
                "status": "error",
                "code": "need_items",
                "message": t("PACK_NEED_ITEMS", lang, id=pack_id, need=need, have=have),
            }
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
        try:
            refresh_report(_rsi_dir(store), store)
        except Exception:
            pass
    return result
