"""Promote: exact-key successes or teaching flag → pending norms only."""

from __future__ import annotations

import json

import pytest
import yaml

from rsi_boot.memory.paths import official_dir, pending_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc


def _pattern(store: MemoryStore, doc_id: str, sig: str, ft: str) -> MemoryDoc:
    dest = official_dir(store.rsi_dir, "pattern") / memory_filename("列出列名", doc_id)
    return store.write(
        MemoryDoc(
            id=doc_id,
            type="pattern",
            title="列出列名",
            content="查询必须写列名",
            payload={"error_signature": sig, "failure_type": ft},
        ),
        dest=dest,
    )


def _teaching(
    store: MemoryStore,
    doc_id: str,
    *,
    sig: str = "select-star",
    ft: str = "sql",
    promote: bool = False,
    applies_to: list[str] | None = None,
    title: str = "列出列名",
    scope: str = "project",
) -> MemoryDoc:
    dest = official_dir(store.rsi_dir, "teaching_case") / memory_filename(title, doc_id)
    return store.write(
        MemoryDoc(
            id=doc_id,
            type="teaching_case",
            title=title,
            content="查询必须写列名",
            payload={
                "error_signature": sig,
                "failure_type": ft,
                "scope": scope,
                "trigger": {"error_signature": sig, "failure_type": ft},
                "lesson": {"applies_to": list(applies_to or []), "correct_fix": "写列名"},
                "fix_and_learn": {"promote_to_pattern": promote, "validation": ""},
                "promote_to_pattern": promote,
            },
        ),
        dest=dest,
    )


def _feedback_successes(rsi_dir, sig: str, ft: str, n: int = 3) -> None:
    logs = rsi_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(n):
        action = "accepted" if i % 2 == 0 else "applied"
        lines.append(json.dumps({
            "id": f"{i:032x}",
            "kind": "feedback",
            "action": action,
            "error_signature": sig,
            "failure_type": ft,
        }, ensure_ascii=False))
    (logs / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pending_yaml(tmp_path):
    root = tmp_path / ".rsi" / "memory" / "pending"
    return list(root.rglob("*.yaml")) if root.exists() else []


def _pattern_yaml(tmp_path):
    root = tmp_path / ".rsi" / "memory" / "patterns"
    return list(root.glob("*.yaml")) if root.exists() else []


@pytest.mark.asyncio
async def test_three_successes_writes_pending_not_patterns_no_inject(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    store = MemoryStore(tmp_path / ".rsi")
    source = _pattern(store, "a" * 32, "select-star", "sql")
    before_patterns = {p.read_text(encoding="utf-8") for p in _pattern_yaml(tmp_path)}
    _feedback_successes(store.rsi_dir, "select-star", "sql", 3)

    rewrite_calls: list[object] = []

    async def _spy_rewrite(self, *args, **kwargs):
        rewrite_calls.append((args, kwargs))
        return {"written": 0, "removed": 0}

    monkeypatch.setattr("rsi_boot.injector.rule_injector.RuleInjector.rewrite", _spy_rewrite)

    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {"action": "promote"})
        assert out.get("code") != "not_in_phase"
        assert out["status"] == "success"
        pending = _pending_yaml(tmp_path)
        assert pending, "3 exact-key successes must write pending norms"
        assert all(
            "pending/prohibitions" in p.as_posix()
            or "pending/conventions" in p.as_posix()
            or "pending/skills" in p.as_posix()
            for p in pending
        )
        after_patterns = {p.read_text(encoding="utf-8") for p in _pattern_yaml(tmp_path)}
        assert after_patterns == before_patterns
        assert rewrite_calls == []
        catalog = yaml.safe_load(
            (tmp_path / ".rsi" / "state" / "catalog.yaml").read_text(encoding="utf-8")
        )
        edges = catalog.get("edges") or []
        assert any(
            e.get("rel") == "distilled_from" and e.get("to") == source.id
            for e in edges
        )
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_two_exact_key_successes_write_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    store = MemoryStore(tmp_path / ".rsi")
    _pattern(store, "d" * 32, "select-star", "sql")
    _feedback_successes(store.rsi_dir, "select-star", "sql", 2)

    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {"action": "promote"})
        assert out["status"] == "success"
        assert _pending_yaml(tmp_path) == []
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_mismatched_failure_type_does_not_count_toward_three(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    store = MemoryStore(tmp_path / ".rsi")
    _pattern(store, "e" * 32, "select-star", "sql")
    logs = store.rsi_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({
            "id": f"{i:032x}",
            "kind": "feedback",
            "action": "accepted",
            "error_signature": "select-star",
            "failure_type": "sql",
        }, ensure_ascii=False)
        for i in range(2)
    ]
    lines.append(json.dumps({
        "id": f"{2:032x}",
        "kind": "feedback",
        "action": "accepted",
        "error_signature": "select-star",
        "failure_type": "timeout",
    }, ensure_ascii=False))
    (logs / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {"action": "promote"})
        assert out["status"] == "success"
        assert _pending_yaml(tmp_path) == []
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_teaching_promote_to_pattern_writes_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    store = MemoryStore(tmp_path / ".rsi")
    _teaching(store, "b" * 32, promote=True)
    before = len(_pattern_yaml(tmp_path))

    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {"action": "promote"})
        assert out["status"] == "success"
        assert _pending_yaml(tmp_path)
        assert len(_pattern_yaml(tmp_path)) == before
        assert not list((tmp_path / ".rsi" / "memory" / "prohibitions").glob("*.yaml"))
        assert not list((tmp_path / ".rsi" / "memory" / "conventions").glob("*.yaml"))
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_domain_scope_skips_global_prohibition(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.learn_tool import handle
    from rsi_boot.bootstrap import build_runtime

    store = MemoryStore(tmp_path / ".rsi")
    _teaching(
        store,
        "c" * 32,
        promote=True,
        applies_to=["backend"],
        title="禁止 SELECT *",
        scope="domain",
    )

    rt = await build_runtime(project_root=tmp_path)
    try:
        out = await handle(rt, {"action": "promote", "scope": "domain"})
        assert out["status"] == "success"
        proh = pending_dir(store.rsi_dir, "prohibition")
        assert list(proh.glob("*.yaml")) == []
    finally:
        await rt.close()
