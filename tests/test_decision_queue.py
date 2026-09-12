"""DecisionQueue sticky / suppress / close，以及抉择卡收集优先级。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from rsi_boot.services.decision_queue import (
    DecisionCard,
    DecisionQueue,
    collect_decision_cards,
)


def _card(decision_id: str, kind: str, **kwargs) -> DecisionCard:
    return DecisionCard(
        id=decision_id,
        kind=kind,
        prompt=kwargs.get("prompt", "prompt"),
        options=kwargs.get("options", [{"id": "keep_item", "label": "新"}]),
        recommended=kwargs.get("recommended", "keep_item"),
        recommended_reason=kwargs.get("recommended_reason", "reason"),
        sides=kwargs.get("sides", []),
        impact=kwargs.get("impact", {}),
        more_waiting=kwargs.get("more_waiting", 0),
    )


def test_pick_sticky_prefers_presented_id():
    queue = DecisionQueue()
    card_a = _card("A", "daily_conflict")
    card_b = _card("B", "daily_conflict")
    queue.mark_presented("A")
    picked = queue.pick([card_b, card_a])
    assert picked is not None
    assert picked.id == "A"


def test_suppress_skips_id_and_picks_next():
    queue = DecisionQueue()
    card_a = _card("A", "daily_conflict")
    card_b = _card("B", "bootstrap_conflict")
    queue.suppress("A")
    picked = queue.pick([card_a, card_b])
    assert picked is not None
    assert picked.id == "B"


def test_close_removes_from_pick():
    queue = DecisionQueue()
    card_a = _card("A", "daily_conflict")
    card_b = _card("B", "bootstrap_conflict")
    queue.mark_presented("A")
    queue.close("A")
    picked = queue.pick([card_a, card_b])
    assert picked is not None
    assert picked.id == "B"


def test_pick_priority_daily_over_bootstrap_over_extract():
    queue = DecisionQueue()
    extract = _card("extract:run1", "bootstrap_extract")
    boot = _card("boot-1", "bootstrap_conflict")
    daily = _card("daily-1", "daily_conflict")
    picked = queue.pick([extract, boot, daily])
    assert picked is not None
    assert picked.kind == "daily_conflict"
    leftover = [c for c in (extract, boot, daily) if c.id != picked.id]
    assert queue.pick(leftover).kind == "bootstrap_conflict"


def test_pick_empty_returns_none():
    assert DecisionQueue().pick([]) is None


async def _insert_item(db, *, project_id, title, source_url, tags, status="pending_review"):
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    item_id = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, domain, tags,"
        " source_url, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'convention', NULL, ?, ?, ?, ?, ?)",
        (item_id, project_id, title, f"{title} body", json.dumps(tags, ensure_ascii=False),
         source_url, status, now, now),
    )
    await conn.commit()
    return item_id


async def _insert_conflict(db, *, project_id, item_id, peer_source, conflict_type="incoherent"):
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    cid = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO rule_conflicts (id, project_id, item_id, user_rule_path, user_rule_excerpt,"
        " user_rule_hash, conflict_type, status, resolution_note, detected_at)"
        " VALUES (?, ?, ?, ?, 'excerpt', ?, ?, 'open', 'recommended:keep_item', ?)",
        (cid, project_id, item_id, peer_source, uuid.uuid4().hex, conflict_type, now),
    )
    await conn.commit()
    return cid


async def test_collect_classifies_daily_and_bootstrap_and_extract(db, tmp_path):
    pid = "p1"
    daily_item = await _insert_item(
        db, project_id=pid, title="禁止：pydantic",
        source_url="auto-extract", tags=["signal:conversation"],
    )
    daily_id = await _insert_conflict(db, project_id=pid, item_id=daily_item, peer_source="docs/api.md")

    boot_item = await _insert_item(
        db, project_id=pid, title="文档 v2",
        source_url="docs/v2.md", tags=["signal:docs", "bootstrap_run_id:run-x"],
    )
    boot_id = await _insert_conflict(
        db, project_id=pid, item_id=boot_item, peer_source="docs/v1.md", conflict_type="version",
    )

    run_id = "run-x"
    await _insert_item(
        db, project_id=pid, title="抽取决策",
        source_url="cursor/chat.md",
        tags=["signal:conversation", f"bootstrap_run_id:{run_id}"],
    )
    (tmp_path / "bootstrap_run.json").write_text(
        json.dumps({"latest": run_id}), encoding="utf-8",
    )

    cards = await collect_decision_cards(db, pid, project_root=None)
    by_kind = {c.kind: c for c in cards}
    assert set(by_kind) == {"daily_conflict", "bootstrap_conflict", "bootstrap_extract"}
    assert by_kind["daily_conflict"].id == daily_id
    assert by_kind["bootstrap_conflict"].id == boot_id
    assert by_kind["bootstrap_extract"].id == f"extract:{run_id}"
    assert [o["id"] for o in by_kind["daily_conflict"].options] == [
        "keep_item", "keep_peer", "coexist",
    ]
    assert by_kind["daily_conflict"].recommended == "keep_item"
    assert [o["id"] for o in by_kind["bootstrap_extract"].options] == [
        "approve", "reject", "skip",
    ]
    skip = next(o for o in by_kind["bootstrap_extract"].options if o["id"] == "skip")
    assert "suppress" in (skip.get("label") or skip.get("note") or "").lower() or "跳过" in str(skip)
