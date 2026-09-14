"""知识冲突落库、explain 只读、keep_* / coexist 裁决（Task 4）。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from rsi_boot.api.tools import conflicts_tool
from rsi_boot.bootstrap import build_runtime
from rsi_boot.scanner.conflict_gate import ConflictDraft

from memory_helpers import memory_conflict_rows, write_memory_item


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _insert_knowledge(rt, *, title: str, content: str, source_url: str,
                            status: str, content_type: str = "convention") -> str:
    return write_memory_item(
        rt.store, title=title, content=content, source_url=source_url,
        tags=["signal:docs"], type=content_type, status=status,
    )


def _incoherent_draft(left: str, right: str) -> ConflictDraft:
    return ConflictDraft(
        conflict_type="incoherent",
        left_source=left,
        right_source=right,
        reason="极性相反：禁止 pydantic vs 允许 pydantic",
        hold_sources=[left, right],
        recommended="keep_item",
        recommended_reason="新稿来自刚才这次对话的明确否定",
    )


async def _open_conflict(rt, project_id: str) -> dict:
    del project_id
    rows = [
        r for r in memory_conflict_rows(rt.store.rsi_dir.parent)
        if r.get("status", "open") == "open"
    ]
    assert rows
    return rows[0]


async def _status_by_id(rt, *item_ids: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item_id in item_ids:
        out[item_id] = rt.store.read(item_id).status
    return out


async def _seed_incoherent_pair(rt, left="docs/new.md", right="docs/old.md",
                                left_status="pending_review",
                                right_status="active"):
    left_id = await _insert_knowledge(
        rt, title="禁止：pydantic", content="禁止使用 pydantic 做请求校验。",
        source_url=left, status=left_status, content_type="prohibition",
    )
    right_id = await _insert_knowledge(
        rt, title="API 用 pydantic", content="允许使用 pydantic 做请求校验。",
        source_url=right, status=right_status,
    )
    n = await rt.conflict_detector.persist_knowledge_conflicts(
        rt.project_id, [_incoherent_draft(left, right)],
        {left: left_id, right: right_id},
    )
    assert n == 1
    conflict = await _open_conflict(rt, rt.project_id)
    return left_id, right_id, conflict


@pytest.mark.asyncio
async def test_explain_does_not_mutate(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    try:
        pid = rt.project_id
        left_id, right_id, conflict = await _seed_incoherent_pair(rt)
        conflict_id = conflict["id"]
        before = next(r for r in memory_conflict_rows(root) if r["id"] == conflict_id)
        items_before = {i: rt.store.read(i).status for i in (left_id, right_id)}

        explained = await rt.conflict_detector.explain(conflict_id)
        assert explained is not None
        assert explained["sides"]
        assert "keep_item" in {o["resolution"] for o in explained["options"]}
        assert {o["resolution"] for o in explained["options"]} >= {
            "keep_item", "keep_peer", "coexist",
        }

        after = next(r for r in memory_conflict_rows(root) if r["id"] == conflict_id)
        assert after["status"] == "open"
        assert after.get("resolution_note") == before.get("resolution_note")
        items_after = {i: rt.store.read(i).status for i in (left_id, right_id)}
        assert items_after == items_before
        assert pid
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_persist_knowledge_conflicts_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    try:
        left, right = "docs/new.md", "docs/old.md"
        left_id, right_id, _ = await _seed_incoherent_pair(rt, left, right)
        again = await rt.conflict_detector.persist_knowledge_conflicts(
            rt.project_id, [_incoherent_draft(left, right)],
            {left: left_id, right: right_id},
        )
        assert again == 0
        assert len(memory_conflict_rows(root)) == 1
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_resolve_keep_item_archives_peer_and_activates_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    try:
        left_id, right_id, conflict = await _seed_incoherent_pair(rt)
        result = await rt.conflict_detector.resolve(conflict["id"], "keep_item")
        assert result is not None
        assert result["resolution"] == "keep_item"
        statuses = await _status_by_id(rt, left_id, right_id)
        assert statuses[left_id] == "active"
        assert statuses[right_id] == "archived"
        row = next(r for r in memory_conflict_rows(root) if r["id"] == conflict["id"])
        assert row["status"] == "keep_item"
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_resolve_keep_peer_archives_item_and_activates_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    try:
        left_id, right_id, conflict = await _seed_incoherent_pair(
            rt, right_status="pending_review",
        )
        result = await rt.conflict_detector.resolve(conflict["id"], "keep_peer")
        assert result is not None
        statuses = await _status_by_id(rt, left_id, right_id)
        assert statuses[left_id] == "archived"
        assert statuses[right_id] == "active"
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_resolve_coexist_activates_both_without_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    try:
        left_id, right_id, conflict = await _seed_incoherent_pair(
            rt, right_status="pending_review",
        )
        result = await rt.conflict_detector.resolve(conflict["id"], "coexist")
        assert result is not None
        statuses = await _status_by_id(rt, left_id, right_id)
        assert statuses[left_id] == "active"
        assert statuses[right_id] == "active"
        items = await rt.conflict_detector.list_conflicts(rt.project_id, status="coexist")
        assert len(items) == 1
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_explain_item_peer_key_shows_historical_title(tmp_path, monkeypatch):
    """user_rule_path=item:<hist_id> 时展开应显示历史条目标题，而非空或合成键。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    try:
        hist_title = "API 用 pydantic"
        hist_id = await _insert_knowledge(
            rt, title=hist_title, content="允许使用 pydantic 做请求校验。",
            source_url="", status="active",
        )
        new_id = await _insert_knowledge(
            rt, title="禁止：pydantic", content="禁止使用 pydantic 做请求校验。",
            source_url="auto-extract", status="pending_review",
            content_type="prohibition",
        )
        peer_key = f"item:{hist_id}"
        n = await rt.conflict_detector.persist_knowledge_conflicts(
            rt.project_id,
            [ConflictDraft(
                conflict_type="incoherent",
                left_source="auto-extract",
                right_source=peer_key,
                reason="极性相反：禁止 pydantic vs 允许 pydantic",
                hold_sources=["auto-extract", peer_key],
                recommended="keep_item",
                recommended_reason="新稿来自刚才这次对话的明确否定",
            )],
            {"auto-extract": new_id, peer_key: hist_id},
        )
        assert n == 1
        conflict = await _open_conflict(rt, rt.project_id)
        assert conflict["user_rule_path"] == peer_key

        explained = await rt.conflict_detector.explain(conflict["id"])
        assert explained is not None
        peer_side = next(s for s in explained["sides"] if s["role"] == "peer")
        assert peer_side["title"] == hist_title
        assert peer_side["item_id"] == hist_id
        assert not peer_side["title"].startswith("item:")
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_conflicts_tool_explain_requires_conflict_id(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    try:
        missing = await conflicts_tool.handle(rt, {"action": "explain"})
        assert missing["status"] == "error"

        _, _, conflict = await _seed_incoherent_pair(rt)
        explained = await conflicts_tool.handle(
            rt, {"action": "explain", "conflict_id": conflict["id"]},
        )
        assert explained["status"] == "success"
        assert explained["data"]["sides"]
        assert "keep_item" in {o["resolution"] for o in explained["data"]["options"]}
        row = next(r for r in memory_conflict_rows(root) if r["id"] == conflict["id"])
        assert row["status"] == "open"
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_resolve_coexist_triggers_on_change(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    hits: list[str] = []
    try:
        _, _, conflict = await _seed_incoherent_pair(rt)
        inner = rt.knowledge.on_change

        async def _spy(project_id: str) -> None:
            hits.append(project_id)
            if inner is not None:
                await inner(project_id)

        rt.knowledge.on_change = _spy
        result = await rt.conflict_detector.resolve(conflict["id"], "coexist")
        assert result is not None
        assert hits == [rt.project_id]
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_resolve_keep_item_invalidates_retriever_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    hits = {"n": 0}
    try:
        _, _, conflict = await _seed_incoherent_pair(rt)
        orig = rt.invalidate_index

        def _spy() -> None:
            hits["n"] += 1
            orig()

        rt.invalidate_index = _spy
        result = await rt.conflict_detector.resolve(conflict["id"], "keep_item")
        assert result is not None
        assert hits["n"] >= 1
    finally:
        await rt.close()
