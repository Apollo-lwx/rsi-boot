import pytest
import yaml


@pytest.mark.asyncio
async def test_promote_not_in_phase(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools import learn_tool
    from rsi_boot.bootstrap import build_runtime
    from rsi_boot.ux.messages import t
    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await learn_tool.handle(rt, {"action": "promote"})
        assert out["code"] == "not_in_phase"
        assert out["message"] == t("PHASE_PROMOTE", "zh")
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_teach_record_writes_pair(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime
    from rsi_boot.ux.messages import t
    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {
            "action": "teach_record",
            "lesson": {
                "wrong_action": "SELECT *",
                "correct_fix": "列出列名",
                "error_signature": "select-star",
                "failure_type": "sql",
            },
        })
        assert out["status"] == "success"
        assert out["message"] == t("TEACH_RECORDED", "zh", path=out["data"]["path"])
        assert "teaching-cases" in out["data"]["path"]
        assert out["data"]["closeout"]
        assert list((tmp_path / ".rsi" / "memory" / "gene-map" / "manual").glob("*.yaml"))
        memory_root = tmp_path / ".rsi" / "memory"
        assert not list((memory_root / "prohibitions").glob("*.yaml"))
        assert not list((memory_root / "conventions").glob("*.yaml"))
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_teach_catch_writes_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime
    from rsi_boot.ux.messages import t
    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {
            "action": "teach_catch",
            "trigger": {"error_signature": "select-star", "failure_type": "sql"},
            "system_attempts": [{"attempt": "SELECT *", "result": "too wide"}],
        })
        assert out["status"] == "success"
        draft_id = out["data"]["id"]
        assert out["message"] == t("TEACH_CATCH_SAVED", "zh", id=draft_id)
        draft = tmp_path / ".rsi" / "state" / "teach-drafts" / f"{draft_id}.yaml"
        assert draft.is_file()
        payload = yaml.safe_load(draft.read_text(encoding="utf-8"))
        assert payload["trigger"]["error_signature"] == "select-star"
        assert payload["system_attempts"]
        assert not list((tmp_path / ".rsi" / "memory").rglob("*.yaml"))
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_teach_catch_redacts_secrets_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime
    rt = await build_runtime(project_root=tmp_path)
    try:
        secret = "supersecret99"
        out = await handle(rt, {
            "action": "teach_catch",
            "trigger": {
                "error_signature": "conn-leak",
                "message": f"failed with password={secret}",
            },
            "system_attempts": [{"attempt": f"connect password={secret}", "result": "denied"}],
        })
        assert out["status"] == "success"
        draft = tmp_path / ".rsi" / "state" / "teach-drafts" / f"{out['data']['id']}.yaml"
        raw = draft.read_text(encoding="utf-8")
        assert secret not in raw
        assert "password=****" in raw
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_missing_action_uses_locked_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime
    from rsi_boot.ux.messages import t
    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {})
        assert out["status"] == "error"
        assert out["code"] == "invalid"
        assert out["message"] == t("LEARN_NEED_ACTION", "zh")
        unknown = await handle(rt, {"action": "nope"})
        assert unknown["status"] == "error"
        assert unknown["code"] == "invalid"
        assert unknown["message"] == t("LEARN_NEED_ACTION", "zh")
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_audit_and_extract_are_in_phase(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime
    rt = await build_runtime(project_root=tmp_path)
    try:
        started = await handle(rt, {"action": "audit_start", "scope": "session"})
        assert started.get("code") != "not_in_phase"
        assert started["status"] == "success"
        extracted = await handle(rt, {
            "action": "extract",
            "scope": "session",
            "items": [{
                "kind": "pattern",
                "title": "列出列名",
                "content": "必须写列名",
                "one_liner": "列出列名",
            }],
        })
        assert extracted.get("code") != "not_in_phase"
        assert extracted["status"] == "success"
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_teach_record_requires_correct_fix(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime
    from rsi_boot.ux.messages import t
    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {
            "action": "teach_record",
            "lesson": {"correct_fix": "   ", "error_signature": "x"},
        })
        assert out["status"] == "error"
        assert out["code"] == "invalid"
        assert out["message"] == t("TEACH_NEED_FIX", "zh")
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_skip_closes_draft_without_case(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime
    from rsi_boot.ux.messages import t
    rt = await build_runtime(project_root=tmp_path)
    try:
        caught = await handle(rt, {
            "action": "teach_catch",
            "trigger": {"error_signature": "x", "failure_type": "sql"},
            "system_attempts": [],
        })
        draft_id = caught["data"]["id"]
        out = await handle(rt, {"action": "skip", "id": draft_id, "reason": "dup"})
        assert out["status"] == "success"
        assert out["message"] == t("TEACH_SKIPPED", "zh", id=draft_id)
        assert not (tmp_path / ".rsi" / "state" / "teach-drafts" / f"{draft_id}.yaml").exists()
        assert not list((tmp_path / ".rsi" / "memory" / "teaching-cases").rglob("*.yaml"))
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_teach_record_pending_and_pattern_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime
    rt = await build_runtime(project_root=tmp_path)
    try:
        pending = await handle(rt, {
            "action": "teach_record",
            "low_confidence": True,
            "lesson": {"correct_fix": "列出列名", "error_signature": "select-star"},
        })
        assert pending["status"] == "success"
        assert "teaching-cases/pending" in pending["data"]["path"].replace("\\", "/")
        assert pending["data"].get("status", "active") == "active"

        promoted = await handle(rt, {
            "action": "teach_record",
            "promote_to_pattern": True,
            "lesson": {"correct_fix": "写列名", "error_signature": "star-2"},
        })
        assert promoted["status"] == "success"
        memory_root = tmp_path / ".rsi" / "memory"
        assert list((memory_root / "patterns").glob("*.yaml"))
        assert not list((memory_root / "prohibitions").glob("*.yaml"))
        assert not list((memory_root / "conventions").glob("*.yaml"))
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_rubric_returns_text_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime
    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {"action": "rubric"})
        assert out["status"] == "success"
        text = out["data"]["rubric"]
        assert "pattern" in text
        assert "teaching" in text
        assert not list((tmp_path / ".rsi" / "memory").rglob("*.yaml"))
    finally:
        await rt.close()


def test_list_tools_uses_tool_desc():
    from rsi_boot.api.tools.learn_tool import TOOL_DESCRIPTION
    from rsi_boot.api.tools.recall_tool import TOOL_DESCRIPTION as RECALL_DESC
    from rsi_boot.ux.messages import TOOL_DESC
    assert TOOL_DESCRIPTION == TOOL_DESC["learn"]
    assert RECALL_DESC == TOOL_DESC["recall"]
