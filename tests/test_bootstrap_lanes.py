"""Task 5：bootstrap 写入车道、run id、审批帽隔离。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rsi_boot.bootstrap import build_runtime
from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.core.models import KnowledgeItem
from rsi_boot.data.sqlite import SQLiteClient
from rsi_boot.project import project_scope


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500,
    )
    return argparse.Namespace(**{**defaults, **overrides})


def _long(prefix: str, n: int = 40) -> str:
    return (prefix + "。") * n


def _clean_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "README.md").write_text(
        "# Demo\n\n## 使用\n" + _long("这是一段足够长的仓库原文"),
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        '[project]\ndependencies = ["pydantic>=2"]\n', encoding="utf-8"
    )
    return root


async def _rows(root: Path, sql: str, params: tuple = ()):
    db_path, pid = project_scope(root)
    db = SQLiteClient(db_path)
    try:
        conn = await db.connect()
        async with conn.execute(sql, (pid, *params)) as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def test_clean_repo_docs_and_config_are_active(tmp_path, monkeypatch):
    """干净小仓：README + pyproject → 知识全是 active（或仅规则种子 pending）。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")

    assert await run_bootstrap(_args(root)) == 0

    run_path = root / ".rsi" / "bootstrap_run.json"
    assert run_path.is_file()
    run = json.loads(run_path.read_text(encoding="utf-8"))
    rid = run["latest"]
    assert rid and len(rid) == 32

    rows = await _rows(
        root,
        "SELECT status, content_type, tags, source_url FROM knowledge_items WHERE project_id = ?",
    )
    assert rows
    pending = [r for r in rows if r["status"] == "pending_review"]
    assert all(r["content_type"] == "prohibition" for r in pending)
    assert all(r["status"] in ("active", "pending_review") for r in rows)
    assert any(r["status"] == "active" for r in rows)
    tag = f"bootstrap_run_id:{rid}"
    assert all(tag in (r["tags"] or "") for r in rows)
    config_rows = [r for r in rows if "signal:config" in (r["tags"] or "")]
    assert config_rows
    assert all((r["source_url"] or "") == "signal:config" for r in config_rows)


async def test_auto_extract_pending_survives_cap(tmp_path, monkeypatch):
    """预置 10 条 auto-extract pending，bootstrap 后仍 pending（帽不误伤日常稿）。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    (root / "rsi-boot.yaml").write_text(
        "bootstrap:\n  review_queue_cap: 2\n", encoding="utf-8"
    )

    rt = await build_runtime(project_root=root)
    try:
        for i in range(10):
            await rt.knowledge.add(KnowledgeItem(
                project_id=rt.project_id,
                title=f"日常提取 {i}",
                content=_long(f"从对话抽出的经验 {i}"),
                status="pending_review",
                content_type="experience",
                domain="daily",
                tags=["signal:auto-extract"],
                source_url="auto-extract",
            ))
    finally:
        await rt.close()

    assert await run_bootstrap(_args(root)) == 0

    autos = await _rows(
        root,
        "SELECT status FROM knowledge_items "
        "WHERE project_id = ? AND source_url = 'auto-extract'",
    )
    assert len(autos) == 10
    assert all(r["status"] == "pending_review" for r in autos)

    archived = await _rows(
        root,
        "SELECT id FROM knowledge_items WHERE project_id = ? AND status = 'archived'",
    )
    assert archived == []


async def test_versioned_docs_held_others_active(tmp_path, monkeypatch):
    """foo-v1.0.md + foo-v1.1.md → 这两条 source pending，其它 active。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    (root / "foo-v1.0.md").write_text(
        "# Foo\n\n" + _long("旧版接口返回 xml 且字段名为 user_id"),
        encoding="utf-8",
    )
    (root / "foo-v1.1.md").write_text(
        "# Foo\n\n" + _long("新版接口返回 json 且字段名为 accountId"),
        encoding="utf-8",
    )

    assert await run_bootstrap(_args(root)) == 0

    rows = await _rows(
        root,
        "SELECT status, source_url, tags FROM knowledge_items WHERE project_id = ?",
    )
    held = [
        r for r in rows
        if (r["source_url"] or "").replace("\\", "/") in ("foo-v1.0.md", "foo-v1.1.md")
    ]
    others = [r for r in rows if r not in held]
    assert held
    assert all(r["status"] == "pending_review" for r in held)
    assert others
    for r in others:
        if "signal:rules" in (r["tags"] or ""):
            assert r["status"] == "pending_review"
        else:
            assert r["status"] == "active"

    conflicts = await _rows(
        root,
        "SELECT conflict_type, item_id FROM rule_conflicts WHERE project_id = ?",
    )
    assert any(c["conflict_type"] == "version" for c in conflicts)
    assert all(c["item_id"] for c in conflicts)


async def test_incremental_version_sibling_holds_existing_active(tmp_path, monkeypatch):
    """先学 foo-v1.0.md（active）；再加 foo-v1.1.md → 两侧 pending 且有 version 冲突。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    (root / "foo-v1.0.md").write_text(
        "# Foo\n\n" + _long("旧版接口返回 xml 且字段名为 user_id"),
        encoding="utf-8",
    )

    assert await run_bootstrap(_args(root)) == 0
    first = await _rows(
        root,
        "SELECT status, source_url FROM knowledge_items WHERE project_id = ?",
    )
    v10 = [
        r for r in first
        if (r["source_url"] or "").replace("\\", "/") == "foo-v1.0.md"
    ]
    assert v10
    assert all(r["status"] == "active" for r in v10)

    (root / "foo-v1.1.md").write_text(
        "# Foo\n\n" + _long("新版接口返回 json 且字段名为 accountId"),
        encoding="utf-8",
    )
    assert await run_bootstrap(_args(root)) == 0

    rows = await _rows(
        root,
        "SELECT status, source_url FROM knowledge_items WHERE project_id = ?",
    )
    held = [
        r for r in rows
        if (r["source_url"] or "").replace("\\", "/") in ("foo-v1.0.md", "foo-v1.1.md")
    ]
    assert { (r["source_url"] or "").replace("\\", "/") for r in held } == {
        "foo-v1.0.md", "foo-v1.1.md",
    }
    assert all(r["status"] == "pending_review" for r in held)

    conflicts = await _rows(
        root,
        "SELECT conflict_type, item_id FROM rule_conflicts WHERE project_id = ?",
    )
    assert any(c["conflict_type"] == "version" for c in conflicts)
    assert all(c["item_id"] for c in conflicts)


def _src(row: dict) -> str:
    return (row.get("source_url") or "").replace("\\", "/")


def _multichunk_doc(title: str, note: str) -> str:
    parts = [f"# {title}\n"]
    for i, name in enumerate(("接口", "字段", "错误码"), 1):
        body = (f"{note} 第{i}节{name}说明，旧新口径不同且正文足够长。" * 50)
        parts.append(f"## {name}\n\n{body}\n")
    return "\n".join(parts)


async def test_bootstrap_version_keep_peer_activates_kept(tmp_path, monkeypatch):
    """foo-v1.0 / foo-v1.1：keep_peer 后倾向侧 active，另一侧 archived。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    (root / "foo-v1.0.md").write_text(
        "# Foo\n\n" + _long("旧版接口返回 xml 且字段名为 user_id"),
        encoding="utf-8",
    )
    (root / "foo-v1.1.md").write_text(
        "# Foo\n\n" + _long("新版接口返回 json 且字段名为 accountId"),
        encoding="utf-8",
    )
    assert await run_bootstrap(_args(root)) == 0

    rt = await build_runtime(project_root=root)
    try:
        conn = await rt.db.connect()
        async with conn.execute(
            "SELECT id FROM rule_conflicts "
            "WHERE project_id = ? AND conflict_type = 'version' AND status = 'open'",
            (rt.project_id,),
        ) as cur:
            rows = await cur.fetchall()
        assert len(rows) == 1
        result = await rt.conflict_detector.resolve(rows[0]["id"], "keep_peer")
        assert result is not None
    finally:
        await rt.close()

    held = await _rows(
        root,
        "SELECT status, source_url FROM knowledge_items WHERE project_id = ?",
    )
    v10 = [r for r in held if _src(r) == "foo-v1.0.md"]
    v11 = [r for r in held if _src(r) == "foo-v1.1.md"]
    assert v10 and all(r["status"] == "archived" for r in v10)
    assert v11 and all(r["status"] == "active" for r in v11)


async def test_bootstrap_version_coexist_activates_both(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    (root / "foo-v1.0.md").write_text(
        "# Foo\n\n" + _long("旧版接口返回 xml 且字段名为 user_id"),
        encoding="utf-8",
    )
    (root / "foo-v1.1.md").write_text(
        "# Foo\n\n" + _long("新版接口返回 json 且字段名为 accountId"),
        encoding="utf-8",
    )
    assert await run_bootstrap(_args(root)) == 0

    rt = await build_runtime(project_root=root)
    try:
        conn = await rt.db.connect()
        async with conn.execute(
            "SELECT id FROM rule_conflicts "
            "WHERE project_id = ? AND conflict_type = 'version' AND status = 'open'",
            (rt.project_id,),
        ) as cur:
            rows = await cur.fetchall()
        assert len(rows) == 1
        result = await rt.conflict_detector.resolve(rows[0]["id"], "coexist")
        assert result is not None
    finally:
        await rt.close()

    held = await _rows(
        root,
        "SELECT status, source_url FROM knowledge_items WHERE project_id = ?",
    )
    versions = [r for r in held if _src(r) in ("foo-v1.0.md", "foo-v1.1.md")]
    assert versions
    assert all(r["status"] == "active" for r in versions)


async def test_bootstrap_multichunk_version_one_open_row(tmp_path, monkeypatch):
    """多切片版本对在 persist + trailing scan 后只留一条 open version。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    (root / "foo-v1.0.md").write_text(
        _multichunk_doc("Foo", "旧版 xml user_id"), encoding="utf-8",
    )
    (root / "foo-v1.1.md").write_text(
        _multichunk_doc("Foo", "新版 json accountId"), encoding="utf-8",
    )
    assert await run_bootstrap(_args(root)) == 0

    items = await _rows(
        root,
        "SELECT source_url FROM knowledge_items WHERE project_id = ?",
    )
    v10 = [r for r in items if _src(r) == "foo-v1.0.md"]
    v11 = [r for r in items if _src(r) == "foo-v1.1.md"]
    assert len(v10) > 1 and len(v11) > 1

    conflicts = await _rows(
        root,
        "SELECT id FROM rule_conflicts "
        "WHERE project_id = ? AND conflict_type = 'version' AND status = 'open'",
    )
    assert len(conflicts) == 1


async def test_dry_run_skips_write_and_gate(tmp_path, monkeypatch):
    """dry-run：不写库、不跑 gate、不落 run id。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    called = {"gate": 0}

    import rsi_boot.cli.bootstrap_command as cmd

    orig = cmd.gate_drafts if hasattr(cmd, "gate_drafts") else None

    def _boom(*_a, **_k):
        called["gate"] += 1
        raise AssertionError("dry-run 不得调用 gate_drafts")

    if orig is not None:
        monkeypatch.setattr(cmd, "gate_drafts", _boom)
    else:
        monkeypatch.setattr(
            "rsi_boot.scanner.conflict_gate.gate_drafts", _boom, raising=False
        )

    assert await run_bootstrap(_args(root, dry_run=True)) == 0
    assert called["gate"] == 0
    assert not (root / ".rsi" / "bootstrap_run.json").exists()
    assert not (root / ".rsi" / "manifest.json").exists()
    assert not (root / ".rsi" / "rsi.db").exists()
