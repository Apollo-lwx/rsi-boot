"""P1.12 数据生命周期：归档任务 + 导出 + 知识删除工具。"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rsi_boot.api.tools import knowledge_tool
from rsi_boot.core.models import KnowledgeItem
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.scheduler.tasks import archive_old_logs
from rsi_boot.services.knowledge_service import KnowledgeService


async def _insert_log(db, log_id: str, created_at: str, status: str = "success"):
    conn = await db.connect()
    await conn.execute(
        "INSERT INTO interaction_logs (id, request_id, user_id, raw_input, latency_ms, status, feedback_token, created_at) "
        "VALUES (?, ?, 'u1', 'test', 1, ?, ?, ?)",
        (log_id, log_id, status, f"tok-{log_id}", created_at),
    )
    await conn.commit()


async def test_archive_old_logs(db, tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    await _insert_log(db, "old-1", old)
    await _insert_log(db, "old-2", old)
    await _insert_log(db, "new-1", recent)
    # pending 不归档（崩溃对账由 Phase 3 离线任务处理）
    await _insert_log(db, "pending-1", old, status="pending")

    archived = await archive_old_logs(db, tmp_path / "archive")
    assert archived == 2

    # 导出文件按月命名、内容可读
    month = old[:7].replace("-", "")
    lines = (tmp_path / "archive" / f"{month}.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"].startswith("old-")

    # 库中只剩新日志与 pending
    conn = await db.connect()
    async with conn.execute("SELECT id FROM interaction_logs ORDER BY id") as cur:
        remaining = {r[0] for r in await cur.fetchall()}
    assert remaining == {"new-1", "pending-1"}


async def test_archive_nothing_to_do(db, tmp_path):
    await _insert_log(db, "new-1", datetime.now(timezone.utc).isoformat())
    assert await archive_old_logs(db, tmp_path / "archive") == 0
    assert not (tmp_path / "archive").exists()


async def test_knowledge_delete_tool(db, base_config):
    retriever = KnowledgeRetriever(db, base_config)
    service = KnowledgeService(db, retriever)
    item_id = await service.add(KnowledgeItem(project_id="p1", title="t", content="c"))

    ok = await knowledge_tool.handle(service, {"id": item_id, "project_id": "p1"})
    assert ok["status"] == "success"

    again = await knowledge_tool.handle(service, {"id": item_id, "project_id": "p1"})
    assert again["status"] == "error"

    missing = await knowledge_tool.handle(service, {"project_id": "p1"})
    assert missing["status"] == "error"


async def test_export_service(db, tmp_path):
    import pytest

    from rsi_boot.services.export_service import export_table

    await _insert_log(db, "e1", datetime.now(timezone.utc).isoformat())
    out = tmp_path / "out.json"
    count = await export_table("interaction_logs", "json", out, db)
    assert count == 1
    assert json.loads(out.read_text(encoding="utf-8"))[0]["id"] == "e1"

    out_csv = tmp_path / "out.csv"
    await export_table("interaction_logs", "csv", out_csv, db)
    assert "e1" in out_csv.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="不支持"):
        await export_table("sqlite_master", "json", tmp_path / "x.json", db)
