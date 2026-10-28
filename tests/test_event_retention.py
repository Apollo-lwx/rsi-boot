"""events.jsonl 超过保留期后先导出再从活动日志删除。"""

import uuid
from datetime import datetime, timedelta, timezone

from rsi_boot.memory.logstore import append_event, archive_expired_events, iter_events


def test_archive_expired_events_keeps_recent(store, tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    append_event(store.rsi_dir, {
        "id": uuid.uuid4().hex, "kind": "recall", "ts": old, "task": "旧",
        "retrieved": ["a" * 32], "status": "success",
    })
    append_event(store.rsi_dir, {
        "id": uuid.uuid4().hex, "kind": "recall", "ts": recent, "task": "新",
        "retrieved": ["b" * 32], "status": "success",
    })
    archive = tmp_path / "archive"
    assert archive_expired_events(store.rsi_dir, archive, retention_days=90) == 1
    tasks = [event.get("task") for event in iter_events(store.rsi_dir)]
    assert tasks == ["新"]
    archived = list(archive.glob("*.jsonl"))
    assert len(archived) == 1
    assert "旧" in archived[0].read_text(encoding="utf-8")
