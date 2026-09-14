"""File-backend stats: events.jsonl + yaml artifacts/proposals, no sqlite."""

from datetime import datetime, timezone

import pytest

from rsi_boot.memory.logstore import append_event
from rsi_boot.memory.store import MemoryStore
from rsi_boot.services.stats_service import StatsService


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.asyncio
async def test_summary_from_events_without_sqlite(tmp_path):
    rsi = tmp_path / ".rsi"
    store = MemoryStore(rsi)
    doc = "c" * 32
    ts = _now_iso()
    append_event(rsi, {
        "id": "e1", "kind": "recall", "retrieved": [doc], "ts": ts,
        "action": "accepted", "feedback_action": "accepted", "latency_ms": 12,
    })
    append_event(rsi, {
        "id": "e2", "kind": "recall", "retrieved": [doc], "ts": ts,
        "action": "rejected", "feedback_action": "rejected", "latency_ms": 20,
    })
    append_event(rsi, {
        "id": "e3", "kind": "recall", "retrieved": [doc], "ts": ts, "latency_ms": 8,
    })

    summary = await StatsService(store=store).summary("week")
    assert summary["recalls"] == 3
    assert summary["adoption_rate"] == pytest.approx(0.5)
    assert set(summary) >= {
        "recalls", "avg_recall_latency_ms", "adoption_rate",
        "prohibition_compliance", "prohibition_surfaced",
        "rule_artifacts_active", "proposal_pass_rate", "memory_inventory",
    }
