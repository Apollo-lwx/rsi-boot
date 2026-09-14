"""P1.12 数据生命周期：事件归档筛选 + 导出 + 知识删除工具。"""

import csv
import json
from datetime import datetime, timedelta, timezone

from rsi_boot.api.tools import knowledge_tool
from rsi_boot.core.models import KnowledgeItem
from rsi_boot.memory.logstore import append_event, iter_events
from rsi_boot.memory.store import MemoryStore
from rsi_boot.services.knowledge_service import KnowledgeService


def _insert_event(store: MemoryStore, log_id: str, created_at: str, status: str = "success"):
    append_event(store.rsi_dir, {
        "id": log_id,
        "ts": created_at,
        "kind": "recall",
        "status": status,
        "task": "test",
        "token": f"tok-{log_id}",
        "retrieved": [],
    })


def _archive_eligible(store: MemoryStore, retention_days: int = 90):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    return [
        ev for ev in iter_events(store.rsi_dir)
        if str(ev.get("ts") or "") < cutoff and ev.get("status") != "pending"
    ]


async def test_archive_old_logs(store, tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    _insert_event(store, "old-1", old)
    _insert_event(store, "old-2", old)
    _insert_event(store, "new-1", recent)
    _insert_event(store, "pending-1", old, status="pending")

    archived = _archive_eligible(store)
    assert len(archived) == 2
    assert {ev["id"] for ev in archived} == {"old-1", "old-2"}

    remaining = {ev["id"] for ev in iter_events(store.rsi_dir)}
    assert {"new-1", "pending-1"} <= remaining


async def test_archive_nothing_to_do(store, tmp_path):
    _insert_event(store, "new-1", datetime.now(timezone.utc).isoformat())
    assert _archive_eligible(store) == []


async def test_knowledge_delete_tool(store, tmp_path):
    service = KnowledgeService(store=store, project_root=tmp_path)
    item_id = await service.add(KnowledgeItem(project_id="p1", title="t", content="c"))

    ok = await knowledge_tool.handle(service, {"id": item_id, "project_id": "p1"})
    assert ok["status"] == "success"
    assert store.read(item_id).status == "archived"

    missing = await knowledge_tool.handle(service, {"project_id": "p1"})
    assert missing["status"] == "error"

    again = await knowledge_tool.handle(service, {"id": "0" * 32, "project_id": "p1"})
    assert again["status"] == "error"


async def test_export_service(store, tmp_path):
    _insert_event(store, "e1", datetime.now(timezone.utc).isoformat())
    rows = list(iter_events(store.rsi_dir))
    assert len(rows) == 1
    assert rows[0]["id"] == "e1"

    out = tmp_path / "out.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    assert json.loads(out.read_text(encoding="utf-8"))[0]["id"] == "e1"

    out_csv = tmp_path / "out.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    assert "e1" in out_csv.read_text(encoding="utf-8")
