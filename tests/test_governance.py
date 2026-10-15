"""记忆治理：负反馈回流降置信（#2）+ 失活候选报告（#3）。"""

from datetime import datetime, timezone

import pytest

from rsi_boot.learning.governance import is_low_confidence, mark_low_confidence
from rsi_boot.memory.logstore import append_event
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.services.recall_service import RecallService
from rsi_boot.services.stats_service import StatsService


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(store, *, idx, title, content="正文", status="active"):
    doc_id = f"{idx:08x}{'d' * 24}"
    doc = MemoryDoc(id=doc_id, type="convention", title=title, content=content, status=status)
    base = official_dir(store.rsi_dir, "convention")
    if status == "archived":
        base = store.rsi_dir / "memory" / "archive"
    store.write(doc, dest=base / memory_filename(title, doc_id))
    return doc_id


# ---------- #2 mark_low_confidence ----------

def test_mark_low_confidence_sets_flag_and_counts(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, idx=1, title="甲")
    assert mark_low_confidence(store, [doc_id], "rejected") == 1
    doc = store.read(doc_id)
    assert doc.extra["low_confidence"] is True
    assert doc.extra["low_confidence_count"] == 1
    assert doc.extra["low_confidence_reason"] == "rejected"
    # 再次反馈累计计数
    assert mark_low_confidence(store, [doc_id], "rejected") == 1
    assert store.read(doc_id).extra["low_confidence_count"] == 2


def test_mark_low_confidence_skips_missing(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    assert mark_low_confidence(store, ["ff" * 16], "x") == 0


def test_recall_payload_surfaces_low_confidence():
    doc = MemoryDoc(id="a" * 32, type="convention", title="甲", content="乙",
                    extra={"low_confidence": True})
    assert is_low_confidence(doc) is True
    for payload in (RecallService._item_payload(doc), RecallService._prohibition_payload(doc),
                    RecallService._case_payload(doc)):
        assert payload.get("low_confidence") is True
    plain = MemoryDoc(id="b" * 32, type="convention", title="甲", content="乙")
    assert RecallService._item_payload(plain).get("low_confidence") is None


@pytest.mark.asyncio
async def test_feedback_worker_rejected_marks_retrieved(tmp_path):
    from rsi_boot.feedback.implicit_tracker import FeedbackWorker, ImplicitEvent

    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, idx=5, title="被拒条")
    append_event(store.rsi_dir, {
        "id": "e" * 32, "kind": "recall", "ts": _now(), "token": "tok-1",
        "retrieved": [doc_id], "project_id": "default", "arm": "recall-balanced",
    })
    worker = FeedbackWorker(store)
    await worker._process_store(ImplicitEvent(feedback_token="tok-1", action="rejected",
                                              comment="不适用"))
    assert store.read(doc_id).extra.get("low_confidence") is True


# ---------- #3 stale candidates ----------

def test_stale_candidates_flags_long_unused(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / ".rsi")
    monkeypatch.setattr("rsi_boot.memory.store._utc_now", lambda: "2020-01-01T00:00:00Z")
    stale_id = _write(store, idx=20, title="僵尸条")           # 旧编辑、从未命中
    hit_id = _write(store, idx=21, title="被命中条")            # 旧编辑、但今天命中
    monkeypatch.undo()
    fresh_id = _write(store, idx=22, title="刚写条")            # 今天写入、未命中
    append_event(store.rsi_dir, {"id": "f" * 32, "kind": "recall", "ts": _now(),
                                 "retrieved": [hit_id]})
    result = StatsService(store)._stale_store(unused_days=30, limit=50)
    ids = {c["id"] for c in result["candidates"]}
    assert ids == {stale_id}          # 仅“久未命中且未再编辑”入选
    assert hit_id not in ids          # 今天命中过 → 排除
    assert fresh_id not in ids        # 刚编辑 → 未过门槛
    assert result["total"] == 1
    assert result["truncated"] is False


def test_stale_candidates_reports_total_and_limit(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / ".rsi")
    monkeypatch.setattr("rsi_boot.memory.store._utc_now", lambda: "2019-01-01T00:00:00Z")
    for i in range(3):
        _write(store, idx=30 + i, title=f"条{i}")
    result = StatsService(store)._stale_store(unused_days=30, limit=2)
    assert result["total"] == 3
    assert result["truncated"] is True
    assert len(result["candidates"]) == 2


@pytest.mark.asyncio
async def test_stats_tool_include_stale_attaches_section(tmp_path, monkeypatch):
    from rsi_boot.api.tools.stats_tool import handle

    store = MemoryStore(tmp_path / ".rsi")
    monkeypatch.setattr("rsi_boot.memory.store._utc_now", lambda: "2018-01-01T00:00:00Z")
    _write(store, idx=40, title="旧条")
    stats = StatsService(store)
    out = await handle(stats, {"period": "week", "include_stale": True})
    assert out["status"] == "success"
    assert out["stale"]["total"] >= 1
