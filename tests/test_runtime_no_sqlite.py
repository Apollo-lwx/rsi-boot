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
    await rt.close()
