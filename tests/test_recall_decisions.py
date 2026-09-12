"""召回携带 decisions、工具描述纪律、注入 rsi-decisions.mdc。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from rsi_boot.api.tools import conflicts_tool, knowledge_review_tool, recall_tool
from rsi_boot.bootstrap import build_runtime
from rsi_boot.injector.targets import CursorRuleTarget, MemoryBundle, MemoryRow
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.services.knowledge_service import KnowledgeService
from rsi_boot.services.recall_service import RecallService
from rsi_boot.strategy.recall import RecallArmSelector

SECRET = "test-secret"


def _services(db, config, decisions=None):
    retriever = KnowledgeRetriever(db, config)
    arms = RecallArmSelector(db)
    knowledge = KnowledgeService(db, retriever)
    recall = RecallService(db, retriever, arms, SECRET, decisions=decisions)
    return recall, knowledge


async def _insert_item(db, *, project_id, title, source_url, tags, status="pending_review"):
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    item_id = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, domain, tags,"
        " source_url, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'convention', NULL, ?, ?, ?, ?, ?)",
        (item_id, project_id, title, f"{title} 内容足够长 " * 4,
         json.dumps(tags, ensure_ascii=False), source_url, status, now, now),
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


async def test_recall_default_decisions_empty(db, base_config):
    recall, _ = _services(db, base_config)
    result = await recall.recall("随便问问缓存", "p1")
    assert result["decisions"] == []


async def test_recall_open_daily_conflict_returns_decision_card(db, base_config):
    item_id = await _insert_item(
        db, project_id="p1", title="禁止：pydantic",
        source_url="auto-extract", tags=["signal:conversation"],
    )
    conflict_id = await _insert_conflict(
        db, project_id="p1", item_id=item_id, peer_source="docs/api.md",
    )
    recall, _ = _services(db, base_config)
    result = await recall.recall("API 校验怎么写", "p1")
    assert len(result["decisions"]) == 1
    card = result["decisions"][0]
    assert card["kind"] == "daily_conflict"
    assert card["id"] == conflict_id
    assert [o["id"] for o in card["options"]] == ["keep_item", "keep_peer", "coexist"]
    assert card["recommended"] == "keep_item"
    assert card["more_waiting"] == 0


async def test_recall_more_waiting_counts_remaining(db, base_config):
    a = await _insert_item(
        db, project_id="p1", title="禁止 A",
        source_url="auto-extract", tags=["signal:conversation"],
    )
    b = await _insert_item(
        db, project_id="p1", title="禁止 B",
        source_url="auto-extract", tags=["signal:conversation"],
    )
    await _insert_conflict(db, project_id="p1", item_id=a, peer_source="docs/a.md")
    await _insert_conflict(db, project_id="p1", item_id=b, peer_source="docs/b.md")
    recall, _ = _services(db, base_config)
    result = await recall.recall("冲突怎么处理", "p1")
    assert result["decisions"][0]["kind"] == "daily_conflict"
    assert result["decisions"][0]["more_waiting"] == 1


def test_recall_tool_description_forbids_user_cli():
    assert "不要让用户自己去终端" in recall_tool.TOOL_DESCRIPTION
    assert "rsi_conflicts" in recall_tool.TOOL_DESCRIPTION
    assert "rsi_knowledge_review" in recall_tool.TOOL_DESCRIPTION
    assert "recommended" in recall_tool.TOOL_DESCRIPTION
    assert "explain" in recall_tool.TOOL_DESCRIPTION


def test_cursor_write_always_emits_decisions_mdc(tmp_path):
    target = CursorRuleTarget(tmp_path)
    target.write(MemoryBundle())
    path = tmp_path / ".cursor" / "rules" / "rsi-decisions.mdc"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "alwaysApply: true" in text
    body = text.split("---", 2)[-1]
    assert len(body) <= 800
    assert "不要让用户自己去终端" in text
    assert "recommended" in text
    assert "explain" in text
    # 纪律短文，不倾倒冲突条目正文
    assert "pydantic" not in text


def test_cursor_empty_rewrite_keeps_decisions_mdc(tmp_path):
    target = CursorRuleTarget(tmp_path)
    target.write(MemoryBundle(prohibitions=[
        MemoryRow(id="abc12345deadbeef", title="p", content="c", content_type="prohibition"),
    ]))
    rules_dir = tmp_path / ".cursor" / "rules"
    names = {p.name for p in rules_dir.glob("rsi-*.mdc")}
    assert "rsi-decisions.mdc" in names
    assert any(n.startswith("rsi-prohibition-") for n in names)
    target.write(MemoryBundle())
    leftover = {p.name for p in rules_dir.glob("rsi-*.mdc")}
    assert leftover == {"rsi-decisions.mdc"}


async def test_resolve_closes_decision_card(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    try:
        item_id = await _insert_item(
            rt.db, project_id=rt.project_id, title="禁止：pydantic",
            source_url="auto-extract", tags=["signal:conversation"],
        )
        conflict_id = await _insert_conflict(
            rt.db, project_id=rt.project_id, item_id=item_id, peer_source="docs/api.md",
        )
        first = await rt.recall.recall("怎么校验", rt.project_id)
        assert first["decisions"][0]["id"] == conflict_id
        result = await conflicts_tool.handle(rt, {
            "action": "resolve",
            "conflict_id": conflict_id,
            "resolution": "keep_item",
            "project_id": rt.project_id,
        })
        assert result["status"] == "success"
        second = await rt.recall.recall("怎么校验", rt.project_id)
        assert second["decisions"] == []
    finally:
        await rt.close()


async def test_knowledge_review_closes_extract_card(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    run_id = "run-rev"
    rsi = root / ".rsi"
    rsi.mkdir()
    (rsi / "bootstrap_run.json").write_text(
        json.dumps({"latest": run_id}), encoding="utf-8",
    )
    rt = await build_runtime(project_root=root)
    try:
        item_id = await _insert_item(
            rt.db, project_id=rt.project_id, title="对话抽取",
            source_url="cursor/chat.md",
            tags=["signal:conversation", f"bootstrap_run_id:{run_id}"],
        )
        first = await rt.recall.recall("开始任务", rt.project_id)
        assert first["decisions"][0]["id"] == f"extract:{run_id}"
        result = await knowledge_review_tool.handle(rt, {
            "id": item_id,
            "action": "approve",
            "project_id": rt.project_id,
        })
        assert result["status"] == "ok"
        second = await rt.recall.recall("开始任务", rt.project_id)
        assert second["decisions"] == []
    finally:
        await rt.close()


async def test_runtime_decisions_survives_config_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    try:
        queue = rt.decisions
        queue.mark_presented("sticky-id")
        (root / "rsi-boot.yaml").write_text("retrieval:\n  top_n: 3\n", encoding="utf-8")
        assert rt.watcher.reload() is True
        assert rt.decisions is queue
        assert rt.recall._decisions is queue
    finally:
        await rt.close()
