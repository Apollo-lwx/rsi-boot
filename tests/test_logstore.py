import json
from datetime import datetime, timezone

import pytest

from rsi_boot.memory.logstore import append_event, iter_events

_DOC_ID = "a" * 32


def test_append_rejects_non_id_retrieved(tmp_path):
    with pytest.raises(ValueError, match="retrieved"):
        append_event(tmp_path, {"id": "e1", "kind": "recall", "retrieved": ["database"]})


def test_append_accepts_ids(tmp_path):
    append_event(tmp_path, {"id": "e1", "kind": "recall", "retrieved": [_DOC_ID], "task": "x"})
    rows = list(iter_events(tmp_path))
    assert rows[0]["retrieved"] == [_DOC_ID]


def test_append_rejects_mixed_retrieved(tmp_path):
    with pytest.raises(ValueError, match="retrieved"):
        append_event(
            tmp_path,
            {"id": "e1", "kind": "recall", "retrieved": [_DOC_ID, "database"]},
        )


def test_append_accepts_retrieved_legacy_tags(tmp_path):
    append_event(
        tmp_path,
        {
            "id": "e1",
            "kind": "recall",
            "retrieved": [_DOC_ID],
            "retrieved_legacy_tags": ["database"],
        },
    )
    rows = list(iter_events(tmp_path))
    assert rows[0]["retrieved"] == [_DOC_ID]
    assert rows[0]["retrieved_legacy_tags"] == ["database"]


def test_append_without_retrieved_writes_line(tmp_path):
    append_event(tmp_path, {"id": "e1", "kind": "feedback", "action": "accepted"})
    rows = list(iter_events(tmp_path))
    assert rows[0]["kind"] == "feedback"
    assert "retrieved" not in rows[0]


def test_iter_skips_bad_lines_and_logs_errors(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "events.jsonl").write_text(
        '{"id":"ok","kind":"recall"}\n'
        "not-json\n"
        '{"id":"ok2","kind":"feedback"}\n',
        encoding="utf-8",
    )
    rows = list(iter_events(tmp_path))
    assert [r["id"] for r in rows] == ["ok", "ok2"]
    err_path = logs / "errors.jsonl"
    assert err_path.is_file()
    err = json.loads(err_path.read_text(encoding="utf-8").splitlines()[0])
    assert "not-json" in err.get("line", "") or "events.jsonl" in str(err.get("path", ""))


def test_iter_filters_kinds(tmp_path):
    append_event(tmp_path, {"id": "e1", "kind": "recall"})
    append_event(tmp_path, {"id": "e2", "kind": "feedback"})
    rows = list(iter_events(tmp_path, kinds={"feedback"}))
    assert [r["id"] for r in rows] == ["e2"]


def test_iter_reads_newest_rolled_and_current(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "events-202001.jsonl").write_text(
        '{"id":"old","kind":"recall"}\n', encoding="utf-8"
    )
    (logs / "events-202608.jsonl").write_text(
        '{"id":"mid","kind":"recall"}\n', encoding="utf-8"
    )
    (logs / "events.jsonl").write_text(
        '{"id":"cur","kind":"feedback"}\n', encoding="utf-8"
    )
    ids = [r["id"] for r in iter_events(tmp_path)]
    assert "old" not in ids
    assert "mid" in ids
    assert "cur" in ids


def test_rolls_events_over_50mb(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    events = logs / "events.jsonl"
    events.write_bytes(b" " * (50 * 1024 * 1024 + 1))
    append_event(tmp_path, {"id": "e2", "kind": "recall", "retrieved": ["b" * 32]})
    yyyymm = datetime.now(timezone.utc).strftime("%Y%m")
    rolled = logs / f"events-{yyyymm}.jsonl"
    assert rolled.is_file()
    assert rolled.stat().st_size >= 50 * 1024 * 1024
    assert events.is_file()
    assert events.stat().st_size < 50 * 1024 * 1024
    rows = list(iter_events(tmp_path))
    assert any(r.get("id") == "e2" for r in rows)
