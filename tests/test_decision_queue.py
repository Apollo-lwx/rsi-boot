"""DecisionQueue sticky / suppress / close，以及抉择卡收集优先级。"""

from __future__ import annotations

import json

from memory_helpers import write_memory_conflict, write_memory_item
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


async def test_collect_classifies_daily_and_bootstrap_and_extract(store):
    pid = "p1"
    daily_item = write_memory_item(
        store, title="禁止：pydantic",
        source_url="auto-extract", tags=["signal:conversation"],
    )
    daily_id = write_memory_conflict(store, item_id=daily_item, peer_source="docs/api.md")

    boot_item = write_memory_item(
        store, title="文档 v2",
        source_url="docs/v2.md", tags=["signal:docs", "bootstrap_run_id:run-x"],
    )
    boot_id = write_memory_conflict(
        store, item_id=boot_item, peer_source="docs/v1.md", conflict_type="version",
    )

    run_id = "run-x"
    write_memory_item(
        store, title="抽取决策",
        source_url="cursor/chat.md",
        tags=["signal:conversation", f"bootstrap_run_id:{run_id}"],
    )
    (store.rsi_dir / "bootstrap_run.json").write_text(
        json.dumps({"latest": run_id}), encoding="utf-8",
    )

    cards = await collect_decision_cards(store, pid)
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
    extract = by_kind["bootstrap_extract"]
    assert extract.impact.get("run_id") == run_id
    extract_ids = [s.get("item_id") for s in extract.sides if s.get("role") == "extract"]
    assert len(extract_ids) == 1
    assert all(extract_ids)


async def test_collect_item_peer_path_uses_real_old_title(store):
    pid = "p1"
    old_id = write_memory_item(
        store, title="API 用 pydantic",
        source_url="", tags=["signal:conversation"], status="active",
    )
    new_id = write_memory_item(
        store, title="禁止：pydantic",
        source_url="auto-extract", tags=["signal:conversation"],
    )
    write_memory_conflict(
        store, item_id=new_id, peer_source=f"item:{old_id}",
        excerpt=f"极性相反 historical_id={old_id}",
    )
    cards = await collect_decision_cards(store, pid)
    daily = next(c for c in cards if c.kind == "daily_conflict")
    old_side = next(s for s in daily.sides if s["role"] == "old")
    assert old_side["title"] == "API 用 pydantic"
    assert not old_side["title"].startswith("item:")


async def test_collect_hash_peer_path_uses_real_old_title(store):
    pid = "p1"
    old_id = write_memory_item(
        store, title="共享文档旧稿",
        source_url="docs/shared.md", tags=["signal:docs"], status="active",
    )
    new_id = write_memory_item(
        store, title="共享文档新稿",
        source_url="auto-extract", tags=["signal:conversation"],
    )
    write_memory_conflict(
        store, item_id=new_id,
        peer_source=f"docs/shared.md#{old_id}",
    )
    cards = await collect_decision_cards(store, pid)
    daily = next(c for c in cards if c.kind == "daily_conflict")
    old_side = next(s for s in daily.sides if s["role"] == "old")
    assert old_side["title"] == "共享文档旧稿"


async def test_collect_historical_id_excerpt_fallback(store):
    pid = "p1"
    old_id = write_memory_item(
        store, title="历史约定",
        source_url="", tags=["signal:conversation"], status="active",
    )
    new_id = write_memory_item(
        store, title="新约定",
        source_url="auto-extract", tags=["signal:conversation"],
    )
    write_memory_conflict(
        store, item_id=new_id,
        peer_source="unresolvable-path",
        excerpt=f"极性相反 historical_id={old_id}",
    )
    cards = await collect_decision_cards(store, pid)
    daily = next(c for c in cards if c.kind == "daily_conflict")
    old_side = next(s for s in daily.sides if s["role"] == "old")
    assert old_side["title"] == "历史约定"


async def test_collect_store_extract_card_includes_conversation_faq(tmp_path):
    from rsi_boot.bootstrap import build_runtime
    from rsi_boot.core.models import KnowledgeItem

    root = tmp_path / "proj"
    root.mkdir()
    run_id = "run-faq-card"
    rsi = root / ".rsi"
    rsi.mkdir()
    (rsi / "bootstrap_run.json").write_text(
        json.dumps({"latest": run_id}), encoding="utf-8",
    )
    rt = await build_runtime(project_root=root)
    try:
        await rt.knowledge.add(KnowledgeItem(
            project_id=rt.project_id, title="对话常见问题",
            content="问答正文需要足够长 " * 5,
            status="pending_review", content_type="faq",
            tags=["signal:conversation", f"bootstrap_run_id:{run_id}"],
            source_url="cursor/chat.md",
        ))
        cards = await collect_decision_cards(
            rt.store, rt.project_id, project_root=root,
        )
        extract = next(c for c in cards if c.kind == "bootstrap_extract")
        extract_ids = [s.get("item_id") for s in extract.sides if s.get("role") == "extract"]
        assert len(extract_ids) == 1
    finally:
        await rt.close()


async def test_version_card_defaults_recommended_keep_peer_without_prefix(store):
    pid = "p1"
    item_id = write_memory_item(
        store, title="Foo v1.0",
        source_url="foo-v1.0.md", tags=["signal:docs", "bootstrap_run_id:run-v"],
    )
    write_memory_item(
        store, title="Foo v1.1",
        source_url="foo-v1.1.md", tags=["signal:docs", "bootstrap_run_id:run-v"],
        status="pending_review",
    )
    write_memory_conflict(
        store, item_id=item_id, peer_source="foo-v1.1.md",
        conflict_type="version", resolution_note=None,
    )
    cards = await collect_decision_cards(store, pid)
    version = next(c for c in cards if c.kind == "bootstrap_conflict")
    assert version.recommended == "keep_peer"
