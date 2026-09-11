"""ISSUE-2：bootstrap 审批队列限量（review_queue_cap）+ rsi_knowledge_review 批量审批。

实仓验证发现单次 bootstrap 产出 2308 条 pending_review，人工逐条审批不可用。
修复：bootstrap 按优先级限量入队（超出置 archived，可批量审批恢复）；
rsi_knowledge_review 支持 ids 数组 / all_pending 批量操作。
"""

import argparse
import json
from pathlib import Path

import pytest

from rsi_boot.api.tools import knowledge_review_tool
from rsi_boot.bootstrap import default_db_path
from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.core.models import KnowledgeItem
from rsi_boot.data.sqlite import SQLiteClient
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.services.knowledge_service import KnowledgeService


def _make_doc_project(tmp_path: Path, doc_count: int = 8) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    for i in range(doc_count):
        (docs / f"guide{i}.md").write_text(
            f"# 指南 {i}\n\n" + f"这是第 {i} 篇足够长的文档内容，" * 20, encoding="utf-8"
        )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["pydantic>=2"]\n', encoding="utf-8"
    )
    (tmp_path / "rsi-boot.yaml").write_text(
        "bootstrap:\n  review_queue_cap: 5\n", encoding="utf-8"
    )
    return tmp_path


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500,
    )
    return argparse.Namespace(**{**defaults, **overrides})


async def _status_counts(db_path: Path, project_id: str) -> dict[str, int]:
    db = SQLiteClient(db_path)
    try:
        conn = await db.connect()
        async with conn.execute(
            "SELECT status, COUNT(*) AS n FROM knowledge_items WHERE project_id = ? GROUP BY status",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
        return {r["status"]: r["n"] for r in rows}
    finally:
        await db.close()


async def test_bootstrap_caps_review_queue(tmp_path, monkeypatch):
    """超出 cap 的条目置 archived；convention 优先级最高，保留在 pending 中"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 0

    counts = await _status_counts(default_db_path(), root.name)
    assert counts.get("pending_review", 0) == 5  # 限量生效
    assert counts.get("archived", 0) >= 3        # 8 篇文档 + 配置摘要 - 5

    db = SQLiteClient(default_db_path())
    try:
        conn = await db.connect()
        async with conn.execute(
            "SELECT content_type FROM knowledge_items WHERE project_id = ? AND status = 'pending_review'",
            (root.name,),
        ) as cur:
            types = [r["content_type"] for r in await cur.fetchall()]
        assert "convention" in types  # 配置摘要按优先级保留
    finally:
        await db.close()


async def test_bootstrap_force_rerun_does_not_requeue_archived(tmp_path, monkeypatch):
    """archived 内容参与去重：force 重跑不再入队，零新增"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 0
    before = await _status_counts(default_db_path(), root.name)

    assert await run_bootstrap(_args(root, force=True)) == 0
    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["knowledge_written"] == 0
    after = await _status_counts(default_db_path(), root.name)
    assert after == before  # 总量不变：archived 未被重新入队


# ---------- KnowledgeService：archived 可审批 + 批量审批 ----------


def _service(db, base_config) -> KnowledgeService:
    return KnowledgeService(db, KnowledgeRetriever(db, base_config))


async def _add(knowledge: KnowledgeService, status: str, content_type: str = "documentation",
               title: str = "条目") -> str:
    return await knowledge.add(KnowledgeItem(
        project_id="p1", title=title, content=f"{title} 的足够长内容 " * 5,
        status=status, content_type=content_type, domain="bootstrap", tags=[],
    ))


async def _status_of(db, item_id: str) -> str:
    conn = await db.connect()
    async with conn.execute(
        "SELECT status FROM knowledge_items WHERE id = ?", (item_id,)
    ) as cur:
        row = await cur.fetchone()
    return row["status"]


async def test_review_approves_archived_item(db, base_config):
    """archived（限量溢出）条目可被单个审批恢复为 active"""
    knowledge = _service(db, base_config)
    item_id = await _add(knowledge, "archived")
    assert await knowledge.review(item_id, "p1", approve=True) == "active"
    assert await _status_of(db, item_id) == "active"


async def test_review_batch_approves_all_pending(db, base_config):
    knowledge = _service(db, base_config)
    ids = [await _add(knowledge, "pending_review", title=f"草稿{i}") for i in range(3)]
    active_id = await _add(knowledge, "active", title="已激活")

    result = await knowledge.review_batch("p1", approve=True)
    assert result["processed"] == 3
    assert result["new_status"] == "active"
    for item_id in ids:
        assert await _status_of(db, item_id) == "active"
    assert await _status_of(db, active_id) == "active"  # 原本 active 不受影响


async def test_review_batch_content_type_filter(db, base_config):
    knowledge = _service(db, base_config)
    faq_id = await _add(knowledge, "pending_review", content_type="faq", title="问答")
    doc_id = await _add(knowledge, "pending_review", content_type="documentation", title="文档")

    result = await knowledge.review_batch("p1", approve=True, content_type="faq")
    assert result["processed"] == 1
    assert await _status_of(db, faq_id) == "active"
    assert await _status_of(db, doc_id) == "pending_review"  # 未命中过滤条件


async def test_review_batch_reject(db, base_config):
    knowledge = _service(db, base_config)
    ids = [await _add(knowledge, "pending_review", title=f"草稿{i}") for i in range(2)]
    result = await knowledge.review_batch("p1", approve=False)
    assert result["processed"] == 2
    assert result["new_status"] == "rejected"
    for item_id in ids:
        assert await _status_of(db, item_id) == "rejected"


async def test_review_batch_include_archived(db, base_config):
    knowledge = _service(db, base_config)
    archived_id = await _add(knowledge, "archived", title="溢出")
    result = await knowledge.review_batch("p1", approve=True)
    assert result["processed"] == 0  # 默认不含 archived
    result = await knowledge.review_batch("p1", approve=True, include_archived=True)
    assert result["processed"] == 1
    assert await _status_of(db, archived_id) == "active"


# ---------- rsi_knowledge_review 工具：批量参数 ----------


async def test_tool_all_pending(db, base_config):
    knowledge = _service(db, base_config)
    for i in range(3):
        await _add(knowledge, "pending_review", title=f"草稿{i}")
    result = await knowledge_review_tool.handle(
        knowledge, {"all_pending": True, "action": "approve", "project_id": "p1"}
    )
    assert result["status"] == "ok"
    assert result["processed"] == 3


async def test_tool_ids_batch(db, base_config):
    knowledge = _service(db, base_config)
    ids = [await _add(knowledge, "pending_review", title=f"草稿{i}") for i in range(2)]
    other = await _add(knowledge, "pending_review", title="不在列表")
    result = await knowledge_review_tool.handle(
        knowledge, {"ids": ids, "action": "reject", "project_id": "p1"}
    )
    assert result["status"] == "ok"
    assert result["processed"] == 2
    assert await _status_of(db, other) == "pending_review"


async def test_tool_single_id_still_works(db, base_config):
    knowledge = _service(db, base_config)
    item_id = await _add(knowledge, "pending_review")
    result = await knowledge_review_tool.handle(
        knowledge, {"id": item_id, "action": "approve", "project_id": "p1"}
    )
    assert result["status"] == "ok"
    assert result["new_status"] == "active"


async def test_tool_missing_target_is_error(db, base_config):
    knowledge = _service(db, base_config)
    result = await knowledge_review_tool.handle(
        knowledge, {"action": "approve", "project_id": "p1"}
    )
    assert result["status"] == "error"
