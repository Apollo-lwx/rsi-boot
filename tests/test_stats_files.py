"""File-backend stats: events.jsonl + yaml artifacts/proposals, no sqlite."""

from datetime import datetime, timezone

import pytest

from rsi_boot.core.models import RSIRequest
from rsi_boot.memory.logstore import append_event
from rsi_boot.memory.store import MemoryStore
from rsi_boot.services.log_service import LogService
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


@pytest.mark.asyncio
async def test_summary_joins_feedback_via_log_service(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    logs = LogService(store=store)
    req = RSIRequest(user_id="u1", project_id="p1", raw_input="how to cache")
    token = "tok-stats-join"
    log_id = await logs.insert_pending(req, token)
    await logs.finalize(
        log_id,
        status="success",
        intent="recall",
        retrieved_tags=["c" * 32],
        latency_ms=9,
        response_excerpt="禁止：裸 SQL",
    )
    assert await logs.apply_feedback(token, "accepted", 5) is True

    summary = await StatsService(store=store).summary("week")
    assert summary["recalls"] == 1
    assert summary["adoption_rate"] is not None
    assert summary["adoption_rate"] == pytest.approx(1.0)
    assert summary["prohibition_surfaced"] == 1
    assert summary["prohibition_compliance"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_prohibition_compliance_uses_joined_feedback_action(tmp_path):
    rsi = tmp_path / ".rsi"
    store = MemoryStore(rsi)
    ts = _now_iso()
    append_event(rsi, {
        "id": "r1", "kind": "recall", "token": "tok-p", "ts": ts,
        "excerpt": "禁止：不要这样", "retrieved": ["d" * 32],
    })
    append_event(rsi, {
        "id": "f1", "kind": "feedback", "token": "tok-p", "ts": ts,
        "action": "rejected", "rating": 1,
    })

    summary = await StatsService(store=store).summary("week")
    assert summary["recalls"] == 1
    assert summary["prohibition_surfaced"] == 1
    assert summary["prohibition_compliance"] == pytest.approx(0.0)
