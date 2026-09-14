"""File-backend LogService: finalize must copy pending join keys."""

import pytest

from rsi_boot.core.models import RSIRequest
from rsi_boot.memory.logstore import iter_events
from rsi_boot.memory.store import MemoryStore
from rsi_boot.services.log_service import LogService

_DOC_ID = "a" * 32


def _request() -> RSIRequest:
    return RSIRequest(user_id="u1", project_id="p1", raw_input="how to cache")


@pytest.mark.asyncio
async def test_finalize_copies_join_keys_from_pending(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    logs = LogService(store=store)
    token = "tok-finalize-1"
    log_id = await logs.insert_pending(_request(), token)

    await logs.finalize(
        log_id,
        status="success",
        intent="recall",
        strategy_name="recall-balanced",
        retrieved_tags=[_DOC_ID, "not-an-id"],
        response_excerpt="ok",
        latency_ms=11,
    )

    finalized = [
        e for e in iter_events(store.rsi_dir)
        if e.get("id") == log_id and e.get("status") != "pending"
    ]
    assert finalized, "finalize must append a completed event"
    row = finalized[-1]
    assert row["token"] == token
    assert row["project_id"] == "p1"
    assert row["user_id"] == "u1"
    assert row["task"] == "how to cache"
    assert row["kind"] == "recall"
    assert row["retrieved"] == [_DOC_ID]
    assert "retrieved_legacy_tags" not in row
