"""知识冲突落库、explain 只读、keep_* / coexist 裁决（Task 4）。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from rsi_boot.api.tools import conflicts_tool
from rsi_boot.bootstrap import build_runtime
from rsi_boot.scanner.conflict_gate import ConflictDraft


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _insert_knowledge(rt, *, title: str, content: str, source_url: str,
                            status: str, content_type: str = "convention") -> str:
    conn = await rt.db.connect()
    item_id = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, domain, tags,"
        " source_url, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, rt.project_id, title, content, content_type, "bootstrap",
         json.dumps(["signal:docs"], ensure_ascii=False), source_url, status, _now(), _now()),
    )
    await conn.commit()
    return item_id


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
    conn = await rt.db.connect()
    async with conn.execute(
        "SELECT * FROM rule_conflicts WHERE project_id = ? AND status = 'open'",
        (project_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return dict(row)


async def _status_by_id(rt, *item_ids: str) -> dict[str, str]:
    conn = await rt.db.connect()
    q = ",".join("?" for _ in item_ids)
    async with conn.execute(
        f"SELECT id, status FROM knowledge_items WHERE id IN ({q})", item_ids,
    ) as cur:
        return {r["id"]: r["status"] for r in await cur.fetchall()}


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
        conn = await rt.db.connect()
        async with conn.execute(
            "SELECT status, resolved_at, resolution_note FROM rule_conflicts WHERE id = ?",
            (conflict_id,),
        ) as cur:
            before = dict(await cur.fetchone())
        async with conn.execute(
            "SELECT id, status, updated_at FROM knowledge_items WHERE id IN (?, ?)",
            (left_id, right_id),
        ) as cur:
            items_before = {r["id"]: dict(r) for r in await cur.fetchall()}

        explained = await rt.conflict_detector.explain(conflict_id)
        assert explained is not None
        assert explained["sides"]
        assert "keep_item" in {o["resolution"] for o in explained["options"]}
        assert {o["resolution"] for o in explained["options"]} >= {
            "keep_item", "keep_peer", "coexist",
        }

        async with conn.execute(
            "SELECT status, resolved_at, resolution_note FROM rule_conflicts WHERE id = ?",
            (conflict_id,),
        ) as cur:
            after = dict(await cur.fetchone())
        assert after["status"] == "open"
        assert after == before
        async with conn.execute(
            "SELECT id, status, updated_at FROM knowledge_items WHERE id IN (?, ?)",
            (left_id, right_id),
        ) as cur:
            items_after = {r["id"]: dict(r) for r in await cur.fetchall()}
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
        conn = await rt.db.connect()
        async with conn.execute(
            "SELECT COUNT(*) AS n FROM rule_conflicts WHERE project_id = ?",
            (rt.project_id,),
        ) as cur:
            assert int((await cur.fetchone())["n"]) == 1
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
        conn = await rt.db.connect()
        async with conn.execute(
            "SELECT status FROM rule_conflicts WHERE id = ?", (conflict["id"],)
        ) as cur:
            assert (await cur.fetchone())["status"] == "keep_item"
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
        conn = await rt.db.connect()
        async with conn.execute(
            "SELECT status FROM rule_conflicts WHERE id = ?", (conflict["id"],)
        ) as cur:
            assert (await cur.fetchone())["status"] == "open"
    finally:
        await rt.close()
