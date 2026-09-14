import pytest


@pytest.mark.asyncio
async def test_memory_open_by_id(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle as learn_handle
    from rsi_boot.api.tools.memory_tool import handle
    from rsi_boot.bootstrap import build_runtime
    rt = await build_runtime(project_root=tmp_path)
    try:
        recorded = await learn_handle(rt, {
            "action": "teach_record",
            "lesson": {
                "wrong_action": "SELECT *",
                "correct_fix": "列出列名",
                "error_signature": "select-star",
                "failure_type": "sql",
            },
        })
        doc_id = recorded["data"]["id"]
        out = await handle(rt, {"action": "open", "id": doc_id})
        assert out["status"] == "success"
        assert out["data"]["id"] == doc_id
        assert out["data"]["type"] == "teaching_case"
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_memory_open_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.memory_tool import handle
    from rsi_boot.bootstrap import build_runtime
    from rsi_boot.ux.messages import t
    rt = await build_runtime(project_root=tmp_path)
    try:
        missing = "a" * 32
        out = await handle(rt, {"action": "open", "id": missing})
        assert out["status"] == "error"
        assert out["message"] == t("NOT_FOUND", "zh", id=missing)
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_graph_not_in_phase(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.memory_tool import handle
    from rsi_boot.bootstrap import build_runtime
    from rsi_boot.ux.messages import t
    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {"action": "graph"})
        assert out["code"] == "not_in_phase"
        assert out["message"] == t("PHASE_GRAPH", "zh")
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_memory_index_and_reindex(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle as learn_handle
    from rsi_boot.api.tools.memory_tool import handle
    from rsi_boot.bootstrap import build_runtime
    from rsi_boot.ux.messages import t
    rt = await build_runtime(project_root=tmp_path)
    try:
        await learn_handle(rt, {
            "action": "teach_record",
            "lesson": {"correct_fix": "列出列名", "error_signature": "select-star"},
        })
        listed = await handle(rt, {"action": "index"})
        assert listed["status"] == "success"
        ids = {row["id"] for row in listed["data"]["items"]}
        assert ids
        rebuilt = await handle(rt, {"action": "reindex"})
        assert rebuilt["status"] == "success"
        assert rebuilt["message"] == t("REINDEX_DONE", "zh")
        assert rt.invert_index()["n"] >= 1
    finally:
        await rt.close()


def test_list_tools_uses_tool_desc():
    from rsi_boot.api.tools.memory_tool import TOOL_DESCRIPTION
    from rsi_boot.ux.messages import TOOL_DESC
    assert TOOL_DESCRIPTION == TOOL_DESC["memory"]
