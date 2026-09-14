import json
import subprocess
from pathlib import Path

import pytest
import yaml

from rsi_boot.learning.pipeline import extract_from_feedback
from rsi_boot.ux.messages import t


def _pending_prefixes() -> tuple[str, ...]:
    return ("memory/pending/prohibitions", "memory/pending/conventions")


def _learn_prefixes() -> tuple[str, ...]:
    return (
        "memory/patterns",
        "memory/gene-map/cases",
        "memory/teaching-cases",
        "audit/learning-extractions",
    )


@pytest.mark.asyncio
async def test_extract_paths_exclusive_from_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {
            "action": "extract",
            "scope": "session",
            "items": [
                {
                    "kind": "pattern",
                    "title": "列出列名",
                    "content": "必须写列名",
                    "one_liner": "列出列名",
                    "payload": {"error_signature": "select-star", "failure_type": "sql"},
                },
                {
                    "kind": "gene",
                    "title": "星号失败",
                    "content": "禁止 SELECT *",
                    "one_liner": "星号失败",
                    "payload": {"error_signature": "select-star-g", "failure_type": "sql"},
                },
                {
                    "kind": "teaching",
                    "title": "纠正星号",
                    "content": "写列名",
                    "one_liner": "纠正星号",
                    "payload": {"error_signature": "select-star-t", "failure_type": "sql"},
                },
                {
                    "kind": "teaching_pending",
                    "title": "待补教学",
                    "content": "待补",
                    "one_liner": "待补教学",
                    "payload": {"error_signature": "pending-t", "failure_type": "sql"},
                },
            ],
        })
        assert out["status"] == "success"
        assert out["message"] == t("EXTRACT_CLOSEOUT", "zh")
        closeout = out["data"]["closeout"]
        assert closeout
        rsi = tmp_path / ".rsi"
        for row in closeout:
            rel = (row["path"] or "").replace("\\", "/")
            assert any(rel.startswith(p) or rel.startswith("memory/" + p.split("/", 1)[-1]) for p in (
                "memory/patterns", "memory/gene-map", "memory/teaching-cases",
            )) or rel.startswith("audit/")
            assert not any(rel.startswith(p) for p in _pending_prefixes())
            disk = rsi / rel if not rel.startswith("memory/") or (rsi / rel).exists() else rsi / rel
            if row["kind"] != "skip_dup":
                assert (rsi / rel).is_file(), rel
        written = [p.relative_to(rsi).as_posix() for p in rsi.rglob("*") if p.is_file()]
        for rel in written:
            assert not any(rel.startswith(p) for p in _pending_prefixes())
        assert any(rel.startswith("audit/learning-extractions") for rel in written)

        fb = extract_from_feedback(
            rsi, action="rejected", comment="不要再用 SELECT *", retrieved=["a" * 32],
        )
        assert fb
        for p in fb:
            rel = p.as_posix()
            assert any(rel.startswith(prefix) for prefix in _pending_prefixes())
            assert not any(rel.startswith(prefix) for prefix in _learn_prefixes()[:3])
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_extract_skip_dup_cites_old_id(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    rt = await build_runtime(project_root=tmp_path)
    try:
        first = await handle(rt, {
            "action": "extract",
            "scope": "session",
            "items": [{
                "kind": "gene",
                "title": "星号失败",
                "content": "禁止 SELECT *",
                "one_liner": "星号失败",
                "payload": {"error_signature": "select-star", "failure_type": "sql"},
            }],
        })
        old_id = first["data"]["closeout"][0].get("id") or yaml.safe_load(
            next((tmp_path / ".rsi" / "memory" / "gene-map" / "cases").glob("*.yaml")).read_text(
                encoding="utf-8"
            )
        )["id"]
        second = await handle(rt, {
            "action": "extract",
            "scope": "session",
            "items": [{
                "kind": "gene",
                "title": "重复星号",
                "content": "又写了一遍",
                "one_liner": "重复",
                "payload": {"error_signature": "select-star", "failure_type": "sql"},
            }],
        })
        assert second["status"] == "success"
        cited = [row for row in second["data"]["closeout"] if row["kind"] == "skip_dup"]
        assert cited
        assert old_id in str(cited[0])
        cases = list((tmp_path / ".rsi" / "memory" / "gene-map" / "cases").glob("*.yaml"))
        assert len(cases) == 1
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_session_pass_forces_disclaimer_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    rt = await build_runtime(project_root=tmp_path)
    try:
        started = await handle(rt, {
            "action": "audit_start",
            "scope": "session",
            "changed_files": ["src/foo.py"],
        })
        assert started["status"] == "success"
        ats = list((tmp_path / ".rsi" / "audit" / "session").glob("ats-*.json"))
        assert ats
        report = await handle(rt, {
            "action": "audit_report",
            "scope": "session",
            "overall": "pass",
            "findings": [],
            "probe_summary": {},
            "disclaimer_partial": False,
        })
        assert report["status"] == "success"
        yamls = list((tmp_path / ".rsi" / "audit" / "session").glob("audit-*.yaml"))
        assert yamls
        body = yaml.safe_load(yamls[0].read_text(encoding="utf-8"))
        assert set(body) >= {"scope", "overall", "findings", "probe_summary", "disclaimer_partial"}
        assert body["scope"] == "session"
        assert body["overall"] == "pass"
        assert body["disclaimer_partial"] is True
        session = tmp_path / ".rsi" / "state" / "audit-session.yaml"
        raw = yaml.safe_load(session.read_text(encoding="utf-8")) if session.is_file() else {}
        assert not raw.get("closed_at")

        finished = await handle(rt, {"action": "audit_finish", "scope": "session"})
        assert finished["status"] == "success"
        closed = yaml.safe_load(session.read_text(encoding="utf-8"))
        assert closed["closed_at"]
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_full_audit_needs_scope_id(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {"action": "audit_start", "scope": "full"})
        assert out["status"] == "error"
        assert out["code"] == "invalid"
        assert out["message"] == t("AUDIT_NEED_SCOPE_ID", "zh")
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_probes_accept_upload_only_no_exec(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    def _boom(*_a, **_k):
        raise AssertionError("must not exec probes")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "call", _boom)

    rt = await build_runtime(project_root=tmp_path)
    try:
        await handle(rt, {"action": "audit_start", "scope": "session", "changed_files": ["a.py"]})
        out = await handle(rt, {
            "action": "audit_probes",
            "scope": "session",
            "probe_results": [{"name": "lint", "ok": True, "command": "rm -rf /"}],
        })
        assert out["status"] == "success"
        assert out.get("code") != "not_in_phase"
    finally:
        await rt.close()


def test_learn_schema_lists_live_actions():
    from rsi_boot.api.tools.learn_tool import INPUT_SCHEMA

    desc = INPUT_SCHEMA["properties"]["action"]["description"]
    for name in (
        "teach_catch", "teach_record", "skip", "rubric",
        "audit_start", "audit_probes", "audit_report", "audit_finish", "extract",
    ):
        assert name in desc


@pytest.mark.asyncio
async def test_extract_nested_trigger_signatures_flatten_and_skip_dup(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    item = {
        "kind": "gene",
        "title": "嵌套签名",
        "content": "禁止 SELECT *",
        "one_liner": "嵌套签名",
        "payload": {"trigger": {"error_signature": "nested-star", "failure_type": "sql"}},
    }
    rt = await build_runtime(project_root=tmp_path)
    try:
        first = await handle(rt, {"action": "extract", "scope": "session", "items": [item]})
        assert first["status"] == "success"
        assert first["data"]["closeout"][0]["kind"] == "gene"
        cases = list((tmp_path / ".rsi" / "memory" / "gene-map" / "cases").glob("*.yaml"))
        assert len(cases) == 1
        stored = yaml.safe_load(cases[0].read_text(encoding="utf-8"))
        payload = stored.get("payload") or {}
        assert payload["error_signature"] == "nested-star"
        assert payload["failure_type"] == "sql"

        second = await handle(rt, {"action": "extract", "scope": "session", "items": [item]})
        assert second["status"] == "success"
        cited = [row for row in second["data"]["closeout"] if row["kind"] == "skip_dup"]
        assert cited
        assert first["data"]["closeout"][0]["id"] in str(cited[0])
        assert len(list((tmp_path / ".rsi" / "memory" / "gene-map" / "cases").glob("*.yaml"))) == 1
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_extract_empty_failure_type_not_wildcard_dup(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    rt = await build_runtime(project_root=tmp_path)
    try:
        filled = await handle(rt, {
            "action": "extract",
            "scope": "session",
            "items": [{
                "kind": "gene",
                "title": "有失败类型",
                "content": "禁止 SELECT *",
                "one_liner": "有失败类型",
                "payload": {"error_signature": "same-star", "failure_type": "sql"},
            }],
        })
        assert filled["status"] == "success"
        assert filled["data"]["closeout"][0]["kind"] == "gene"

        empty = await handle(rt, {
            "action": "extract",
            "scope": "session",
            "items": [{
                "kind": "gene",
                "title": "空失败类型",
                "content": "禁止 SELECT * 空类型",
                "one_liner": "空失败类型",
                "payload": {"error_signature": "same-star", "failure_type": ""},
            }],
        })
        assert empty["status"] == "success"
        assert empty["data"]["closeout"][0]["kind"] != "skip_dup"
        assert empty["data"]["closeout"][0]["kind"] == "gene"
        cases = list((tmp_path / ".rsi" / "memory" / "gene-map" / "cases").glob("*.yaml"))
        assert len(cases) == 2
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_full_audit_scope_id_stays_inside_audit_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    audit_root = (tmp_path / ".rsi" / "audit").resolve()
    rt = await build_runtime(project_root=tmp_path)
    try:
        for scope_id in ("../outside", "foo/bar", r"..\..\escape"):
            started = await handle(rt, {
                "action": "audit_start",
                "scope": "full",
                "scope_id": scope_id,
                "changed_files": ["src/foo.py"],
            })
            assert started["status"] == "success"
            start_rel = started["data"]["path"].replace("\\", "/")
            assert ".." not in Path(start_rel).parts
            start_dest = (tmp_path / ".rsi" / start_rel).resolve()
            assert start_dest.is_file()
            assert start_dest.is_relative_to(audit_root)
            assert len(Path(start_rel).parts) == 3

            reported = await handle(rt, {
                "action": "audit_report",
                "scope": "full",
                "scope_id": scope_id,
                "overall": "fail",
                "findings": [],
                "probe_summary": {},
            })
            assert reported["status"] == "success"
            report_rel = reported["data"]["path"].replace("\\", "/")
            assert ".." not in Path(report_rel).parts
            report_dest = (tmp_path / ".rsi" / report_rel).resolve()
            assert report_dest.is_file()
            assert report_dest.is_relative_to(audit_root)
            assert len(Path(report_rel).parts) == 3

        assert not (tmp_path / "outside").exists()
        assert not (tmp_path / "escape").exists()
        leaked = [
            p for p in tmp_path.rglob("*")
            if p.is_file() and ".rsi" not in p.resolve().parts
        ]
        assert not leaked
        assert not (tmp_path / ".rsi" / "outside").exists()
        assert not list((tmp_path / ".rsi" / "audit" / "foo").glob("**/*"))
    finally:
        await rt.close()
