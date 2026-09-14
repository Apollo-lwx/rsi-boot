"""Runtime hot path must not construct or connect SQLite."""

import pytest


@pytest.mark.asyncio
async def test_build_runtime_does_not_construct_sqlite(monkeypatch, tmp_path):
    hits = {"n": 0}

    def boom(*_a, **_k):
        hits["n"] += 1
        raise AssertionError("sqlite")

    monkeypatch.setattr("rsi_boot.data.sqlite.SQLiteClient.__init__", boom)
    monkeypatch.setattr("rsi_boot.data.sqlite.SQLiteClient.connect", boom)
    from rsi_boot.bootstrap import build_runtime

    rt = await build_runtime(project_root=tmp_path)
    assert hits["n"] == 0
    assert not hasattr(rt, "db")
    recalled = await rt.recall.recall("别把所有列一次查出来", rt.project_id)
    await rt.injector.rewrite(rt.project_id)
    from rsi_boot.api.tools.feedback_tool import handle as feedback_handle

    await feedback_handle(
        rt.logs,
        rt.feedback_secret,
        {"feedback_token": recalled["feedback_token"], "action": "accepted"},
        worker=rt.feedback_worker,
    )
    await rt.conflict_detector.scan(rt.project_id)
    await rt.knowledge.list(rt.project_id)
    await rt.extractor.run_daily()
    assert hits["n"] == 0
    await rt.close()
