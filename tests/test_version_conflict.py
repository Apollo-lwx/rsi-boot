"""多版本知识冲突：不自动归档，提交用户裁决；对话记录只作倾向提示。

场景：同功能 DEPRECATED 契约 vs 现行权威、v1.0 vs v1.1 并排。
系统不得替用户选择；有 MCP 交互记录时提示哪一侧更常被召回/提及。
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from rsi_boot.api.tools import conflicts_tool
from rsi_boot.injector.conflict import ConflictDetector
from rsi_boot.scanner.conflict_gate import ConflictDraft
from rsi_boot.scanner.version_conflict import (
    detect_version_families,
    is_self_deprecated,
    score_conversation_hits,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _add_doc(db, project_id, source_url, title="文档切片", content=None, status="pending_review"):
    conn = await db.connect()
    item_id = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, domain, tags,"
        " source_url, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'documentation', 'bootstrap',"
        " ?, ?, ?, ?, ?)",
        (item_id, project_id, title, content or (title + "足够长的正文内容。" * 8),
         json.dumps(["signal:docs"], ensure_ascii=False), source_url, status, _now(), _now()),
    )
    await conn.commit()
    return item_id


async def _add_log(db, project_id, raw_input, retrieved_tags=None):
    conn = await db.connect()
    lid = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO interaction_logs (id, request_id, user_id, project_id, raw_input,"
        " intent, latency_ms, status, feedback_token, retrieved_tags, created_at)"
        " VALUES (?, ?, 'u', ?, ?, 'recall', 10, 'success', ?, ?, ?)",
        (lid, uuid.uuid4().hex, project_id, raw_input, f"tok-{lid[:8]}",
         json.dumps(retrieved_tags or [], ensure_ascii=False), _now()),
    )
    await conn.commit()


async def _open_version(db, project_id="p1"):
    conn = await db.connect()
    async with conn.execute(
        "SELECT * FROM rule_conflicts WHERE project_id = ? AND status = 'open' AND conflict_type = 'version'",
        (project_id,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


# ---------- 文首自标废弃判定（勿误伤「提到旧版已废弃」的现行权威） ----------


def test_is_self_deprecated_only_when_first_blockquote_declares_it():
    deprecated = (
        "# 旧契约\n\n"
        "> **DEPRECATED（已废弃，勿再作为联调权威）**\n"
        "> 现行请看 [新契约](./current.md)\n"
    )
    current = (
        "# 现行清单\n\n"
        "> 依据 Grant Rules。旧版 `old.md` 已 **DEPRECATED**，勿再作为联调权威。\n"
    )
    assert is_self_deprecated(deprecated) is True
    assert is_self_deprecated(current) is False


def test_detect_deprecated_pairs_with_linked_current(tmp_path):
    (tmp_path / "old.md").write_text(
        "# 旧\n\n> **DEPRECATED（已废弃，勿再作为联调权威）**\n"
        "> 现行契约请以 [current.md](./current.md) 为准。\n",
        encoding="utf-8",
    )
    (tmp_path / "current.md").write_text(
        "# 新\n\n> 旧版 old.md 已 DEPRECATED，勿再作为联调权威。\n",
        encoding="utf-8",
    )
    families = detect_version_families(tmp_path)
    assert len(families) == 1
    fam = families[0]
    assert fam.kind == "deprecated"
    assert fam.legacy_rel == "old.md"
    assert fam.current_rel == "current.md"


def test_detect_versioned_filename_siblings(tmp_path):
    docs = tmp_path / "product"
    docs.mkdir()
    (docs / "资产目录需求文档-v1.0.md").write_text("# v1.0\n\n已确认\n", encoding="utf-8")
    (docs / "资产目录需求文档-v1.1.md").write_text("# v1.1\n\n未确认\n", encoding="utf-8")
    (docs / "无关文档.md").write_text("# 其他\n", encoding="utf-8")
    families = [f for f in detect_version_families(tmp_path) if f.kind == "versioned"]
    assert len(families) == 1
    assert {families[0].legacy_rel, families[0].current_rel} == {
        "product/资产目录需求文档-v1.0.md", "product/资产目录需求文档-v1.1.md",
    }
    assert families[0].current_rel.endswith("v1.1.md")  # 高版本作倾向，非自动归档


# ---------- 对话命中计分 ----------


def test_score_conversation_hits_counts_filename_in_logs():
    logs = [
        {"raw_input": "按 ops-role-staff-api-params 改 grantRules", "retrieved_tags": []},
        {"raw_input": "还是 objectGrants 那套？", "retrieved_tags": ["ops-staff-with-auth-submit-contract"]},
    ]
    assert score_conversation_hits("docs/ops-role-staff-api-params.md", logs) >= 1
    assert score_conversation_hits("docs/ops-staff-with-auth-submit-contract.md", logs) >= 1
    assert score_conversation_hits("docs/unrelated.md", logs) == 0


# ---------- 扫描：只开冲突，不改知识状态 ----------


async def test_scan_opens_version_conflict_without_archiving(db, tmp_path):
    (tmp_path / "old.md").write_text(
        "# 旧\n\n> **DEPRECATED（已废弃，勿再作为联调权威）**\n"
        "> 见 [current.md](./current.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "current.md").write_text("# 新\n\n正文\n", encoding="utf-8")
    old_id = await _add_doc(db, "p1", "old.md", title="旧契约")
    new_id = await _add_doc(db, "p1", "current.md", title="新契约")

    stats = await ConflictDetector(db, tmp_path).scan("p1")
    assert stats["version_detected"] == 1
    conflicts = await _open_version(db)
    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "version"
    # 两侧知识状态原样（不替用户归档）
    conn = await db.connect()
    async with conn.execute("SELECT id, status FROM knowledge_items") as cur:
        rows = {r["id"]: r["status"] for r in await cur.fetchall()}
    assert rows[old_id] == "pending_review" and rows[new_id] == "pending_review"


async def test_scan_does_not_flag_current_doc_that_mentions_deprecated(db, tmp_path):
    (tmp_path / "current.md").write_text(
        "# 现行\n\n> 旧版 ghost.md 已 **DEPRECATED**，勿再作为联调权威。\n",
        encoding="utf-8",
    )
    await _add_doc(db, "p1", "current.md")
    stats = await ConflictDetector(db, tmp_path).scan("p1")
    assert stats["version_detected"] == 0


async def test_scan_idempotent(db, tmp_path):
    (tmp_path / "old.md").write_text(
        "# 旧\n\n> **DEPRECATED（已废弃，勿再作为联调权威）**\n> [current.md](./current.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "current.md").write_text("# 新\n", encoding="utf-8")
    await _add_doc(db, "p1", "old.md")
    await _add_doc(db, "p1", "current.md")
    det = ConflictDetector(db, tmp_path)
    assert (await det.scan("p1"))["version_detected"] == 1
    assert (await det.scan("p1"))["version_detected"] == 0
    assert len(await _open_version(db)) == 1


async def test_conversation_flips_hint(db, tmp_path):
    """对话大量提及低版本时，倾向提示翻转到常被使用的一侧——仍不自动归档"""
    docs = tmp_path / "d"
    docs.mkdir()
    (docs / "spec-v1.0.md").write_text("# v1\n", encoding="utf-8")
    (docs / "spec-v1.1.md").write_text("# v1.1\n", encoding="utf-8")
    await _add_doc(db, "p1", "d/spec-v1.0.md")
    await _add_doc(db, "p1", "d/spec-v1.1.md")
    for _ in range(5):
        await _add_log(db, "p1", "继续按 spec-v1.0 的 objectGrants 做")

    await ConflictDetector(db, tmp_path).scan("p1")
    c = (await _open_version(db))[0]
    # item 一侧 = 倾向过时；path 一侧 = 倾向仍有效。对话把 v1.0 打热 → path 应为 v1.0
    assert c["user_rule_path"].replace("\\", "/").endswith("spec-v1.0.md")
    assert "对话" in (c["user_rule_excerpt"] or "")


# ---------- 裁决 ----------


async def test_resolve_keep_peer_archives_legacy_source_only(db, tmp_path):
    (tmp_path / "old.md").write_text(
        "# 旧\n\n> **DEPRECATED（已废弃，勿再作为联调权威）**\n> [current.md](./current.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "current.md").write_text("# 新\n", encoding="utf-8")
    old1 = await _add_doc(db, "p1", "old.md", title="旧-1")
    old2 = await _add_doc(db, "p1", "old.md", title="旧-2")
    new_id = await _add_doc(db, "p1", "current.md", title="新")
    det = ConflictDetector(db, tmp_path)
    await det.scan("p1")
    cid = (await _open_version(db))[0]["id"]

    result = await det.resolve(cid, "keep_peer")
    assert result is not None and "归档" in result["guidance"]
    conn = await db.connect()
    async with conn.execute("SELECT id, status FROM knowledge_items") as cur:
        rows = {r["id"]: r["status"] for r in await cur.fetchall()}
    assert rows[old1] == "archived" and rows[old2] == "archived"
    assert rows[new_id] == "active"


async def test_resolve_keep_item_archives_peer_instead(db, tmp_path):
    (tmp_path / "old.md").write_text(
        "# 旧\n\n> **DEPRECATED（已废弃，勿再作为联调权威）**\n> [current.md](./current.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "current.md").write_text("# 新\n", encoding="utf-8")
    old_id = await _add_doc(db, "p1", "old.md")
    new_id = await _add_doc(db, "p1", "current.md")
    det = ConflictDetector(db, tmp_path)
    await det.scan("p1")
    cid = (await _open_version(db))[0]["id"]
    await det.resolve(cid, "keep_item")
    conn = await db.connect()
    async with conn.execute("SELECT id, status FROM knowledge_items") as cur:
        rows = {r["id"]: r["status"] for r in await cur.fetchall()}
    assert rows[old_id] == "active"
    assert rows[new_id] == "archived"


async def test_resolve_coexist_activates_both(db, tmp_path):
    (tmp_path / "old.md").write_text(
        "# 旧\n\n> **DEPRECATED（已废弃，勿再作为联调权威）**\n> [current.md](./current.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "current.md").write_text("# 新\n", encoding="utf-8")
    old_id = await _add_doc(db, "p1", "old.md")
    new_id = await _add_doc(db, "p1", "current.md")
    det = ConflictDetector(db, tmp_path)
    await det.scan("p1")
    await det.resolve((await _open_version(db))[0]["id"], "coexist")
    conn = await db.connect()
    async with conn.execute("SELECT id, status FROM knowledge_items") as cur:
        rows = {r["id"]: r["status"] for r in await cur.fetchall()}
    assert rows[old_id] == rows[new_id] == "active"
    items = await det.list_conflicts("p1", status="coexist")
    assert items and items[0]["conflict_type"] == "version"


async def test_scan_does_not_duplicate_persist_version_family(db, tmp_path):
    """persist 与 scan 用不同 chunk item_id 时，同一 source pair 只留一条 open version。"""
    (tmp_path / "foo-v1.0.md").write_text("# Foo\n\nv1.0 body\n", encoding="utf-8")
    (tmp_path / "foo-v1.1.md").write_text("# Foo\n\nv1.1 body\n", encoding="utf-8")
    conn = await db.connect()
    early, late = "2020-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"
    for item_id, source, created in (
        ("zzzzpersistid00000000000000000001", "foo-v1.0.md", early),
        ("aaaascanfirstid000000000000000001", "foo-v1.0.md", late),
        ("bbbbv11chunk000000000000000000001", "foo-v1.1.md", early),
        ("ccccv11chunk000000000000000000002", "foo-v1.1.md", late),
    ):
        await conn.execute(
            "INSERT INTO knowledge_items (id, project_id, title, content, content_type, domain, tags,"
            " source_url, status, created_at, updated_at) VALUES (?, 'p1', ?, ?, 'documentation',"
            " 'bootstrap', ?, ?, 'pending_review', ?, ?)",
            (item_id, source, source + "足够长的正文内容。" * 8,
             json.dumps(["signal:docs"], ensure_ascii=False), source, created, created),
        )
    await conn.commit()

    det = ConflictDetector(db, tmp_path)
    n = await det.persist_knowledge_conflicts(
        "p1",
        [ConflictDraft(
            conflict_type="version",
            left_source="foo-v1.0.md",
            right_source="foo-v1.1.md",
            reason="versioned siblings",
            hold_sources=["foo-v1.0.md", "foo-v1.1.md"],
            recommended="keep_peer",
            recommended_reason="倾向 v1.1",
        )],
        {"foo-v1.0.md": "zzzzpersistid00000000000000000001",
         "foo-v1.1.md": "bbbbv11chunk000000000000000000001"},
    )
    assert n == 1
    await det.scan("p1")
    assert len(await _open_version(db)) == 1


async def test_conflicts_tool_version_resolve(db, tmp_path):
    (tmp_path / "old.md").write_text(
        "# 旧\n\n> **DEPRECATED（已废弃，勿再作为联调权威）**\n> [current.md](./current.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "current.md").write_text("# 新\n", encoding="utf-8")
    await _add_doc(db, "p1", "old.md")
    await _add_doc(db, "p1", "current.md")
    runtime = SimpleNamespace(conflict_detector=ConflictDetector(db, tmp_path))
    scan = await conflicts_tool.handle(runtime, {"action": "scan", "project_id": "p1"})
    assert scan["data"]["version_detected"] == 1
    listed = await conflicts_tool.handle(runtime, {"action": "list", "project_id": "p1"})
    cid = listed["data"]["conflicts"][0]["id"]
    resolved = await conflicts_tool.handle(runtime, {
        "action": "resolve", "conflict_id": cid, "resolution": "keep_peer",
    })
    assert resolved["status"] == "success"
