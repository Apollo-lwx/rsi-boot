from pathlib import Path

import pytest

from rsi_boot.learning.reading_pack_actions import pack_done, pack_list, pack_open
from rsi_boot.memory.store import MemoryStore
from rsi_boot.scanner.reading_packs import write_packs
from rsi_boot.ux.messages import t


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / ".rsi")


def _seed(store: MemoryStore, packs: list[dict]) -> None:
    write_packs(store.rsi_dir, bootstrap_run_id="run-1", packs=packs)


def test_pack_list_empty_dir_does_not_raise(tmp_path):
    store = _store(tmp_path)
    (store.rsi_dir / "memory" / "documentation").mkdir(parents=True)
    (store.rsi_dir / "memory" / "documentation" / "ghost.yaml").write_text("id: x\n", encoding="utf-8")
    out = pack_list(store, "zh")
    assert out["status"] == "success"
    assert out["data"] == {
        "total": 0,
        "done": 0,
        "pending": 0,
        "skipped": 0,
        "packs": [],
    }
    assert out["message"] == t("PACK_NEED_BOOTSTRAP", "zh")


def test_pack_open_unknown_is_not_found(tmp_path):
    store = _store(tmp_path)
    _seed(store, [{
        "id": "auth",
        "domain": "auth",
        "title": "认证",
        "sources": [{"kind": "docs", "path": "docs/a.md", "heading": "A"}],
    }])
    out = pack_open(store, "missing", "zh")
    assert out == {
        "status": "error",
        "code": "not_found",
        "message": t("PACK_NOT_FOUND", "zh"),
    }


def test_pack_done_is_idempotent(tmp_path):
    store = _store(tmp_path)
    _seed(store, [{
        "id": "auth",
        "domain": "auth",
        "title": "认证",
        "sources": [{"kind": "docs", "path": "docs/a.md", "heading": "A"}],
    }])
    first = pack_done(store, "auth", status="done", lang="zh")
    assert first["status"] == "success"
    assert first["pack_status"] == "done"
    second = pack_done(store, "auth", status="done", lang="zh")
    assert second["status"] == "success"
    assert second["pack_status"] == "done"
    listed = pack_list(store, "zh")
    assert listed["data"]["done"] == 1
    assert listed["data"]["pending"] == 0


def test_pack_list_counts_statuses(tmp_path):
    store = _store(tmp_path)
    _seed(store, [
        {
            "id": "a",
            "domain": "a",
            "title": "A",
            "sources": [{"kind": "docs", "path": "a.md", "heading": "A"}],
        },
        {
            "id": "b",
            "domain": "b",
            "title": "B",
            "sources": [{"kind": "docs", "path": "b.md", "heading": "B"}],
        },
        {
            "id": "c",
            "domain": "c",
            "title": "C",
            "sources": [{"kind": "docs", "path": "c.md", "heading": "C"}],
        },
    ])
    pack_done(store, "a", status="done")
    pack_done(store, "b", status="skipped", reason="写不出")
    out = pack_list(store, "zh")
    assert out["data"]["total"] == 3
    assert out["data"]["done"] == 1
    assert out["data"]["skipped"] == 1
    assert out["data"]["pending"] == 1
    by_id = {p["id"]: p for p in out["data"]["packs"]}
    assert by_id["a"]["status"] == "done"
    assert by_id["b"]["status"] == "skipped"
    assert by_id["c"]["status"] == "pending"
    opened = pack_open(store, "c", "zh")
    assert opened["status"] == "success"
    assert opened["data"]["id"] == "c"
    assert "content" not in opened["data"]["sources"][0]


@pytest.mark.asyncio
async def test_learn_tool_dispatches_pack_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools import learn_tool
    from rsi_boot.bootstrap import build_runtime

    rt = await build_runtime(project_root=tmp_path)
    try:
        empty = await learn_tool.handle(rt, {"action": "pack_list"})
        assert empty["status"] == "success"
        assert empty["data"]["total"] == 0
        write_packs(rt.store.rsi_dir, bootstrap_run_id="run-1", packs=[{
            "id": "auth",
            "domain": "auth",
            "title": "认证",
            "sources": [{"kind": "docs", "path": "docs/a.md", "heading": "A"}],
        }])
        listed = await learn_tool.handle(rt, {"action": "pack_list"})
        assert listed["data"]["pending"] == 1
        opened = await learn_tool.handle(rt, {"action": "pack_open", "id": "auth"})
        assert opened["data"]["id"] == "auth"
        done = await learn_tool.handle(rt, {"action": "pack_done", "id": "auth"})
        assert done["pack_status"] == "done"
        missing = await learn_tool.handle(rt, {"action": "pack_open", "id": "nope"})
        assert missing["code"] == "not_found"
    finally:
        await rt.close()
