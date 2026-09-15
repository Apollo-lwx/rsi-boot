"""Task 5：bootstrap 写入车道、run id、审批帽隔离。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rsi_boot.bootstrap import build_runtime
from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.core.models import KnowledgeItem

from memory_helpers import archive_memory_docs, memory_conflict_rows, memory_rows_from_sql


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500, include=[],
        host_judge=False, local_judge=True,
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
    return await memory_rows_from_sql(root, sql, params)


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
    """5a：不再用 Jaccard 把版本家族 hold 成 pending，也不预灌 version 冲突。"""
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
    family = [
        r for r in rows
        if _src(r).startswith("foo-v1.0.md") or _src(r).startswith("foo-v1.1.md")
    ]
    assert family
    assert all(r["status"] == "active" for r in family)


async def test_incremental_version_sibling_holds_existing_active(tmp_path, monkeypatch):
    """5a：增量出现版本兄弟也不再 hold / 预灌 version 冲突。"""
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
    v10 = [r for r in first if _src(r).startswith("foo-v1.0.md")]
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
    family = [
        r for r in rows
        if _src(r).startswith("foo-v1.0.md") or _src(r).startswith("foo-v1.1.md")
    ]
    assert { _src(r).split("#", 1)[0] for r in family } == {
        "foo-v1.0.md", "foo-v1.1.md",
    }
    assert all(r["status"] == "active" for r in family)


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
        rows = [
            r for r in memory_conflict_rows(root)
            if (r.get("conflict_type") or r.get("type")) == "version"
            and r.get("status", "open") == "open"
        ]
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
        rows = [
            r for r in memory_conflict_rows(root)
            if (r.get("conflict_type") or r.get("type")) == "version"
            and r.get("status", "open") == "open"
        ]
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


async def test_force_does_not_revive_untagged_overflow_archive(tmp_path, monkeypatch):
    """旧 cap 溢出 archived（无 bootstrap_run_id）在 --force 同哈希时不得复活为 active。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 0
    docs = await _rows(
        root,
        "SELECT id, tags FROM knowledge_items "
        "WHERE project_id = ? AND tags LIKE '%signal:docs%'",
    )
    assert docs
    archive_memory_docs(root, [r["id"] for r in docs], clear_tags=True)

    assert await run_bootstrap(_args(root, force=True)) == 0
    leftover = await _rows(
        root,
        "SELECT id, status FROM knowledge_items WHERE project_id = ? AND id IN ("
        + ",".join("?" * len(docs)) + ")",
        tuple(r["id"] for r in docs),
    )
    assert leftover
    assert all(r["status"] == "archived" for r in leftover)


async def test_force_does_not_stamp_manifest_for_untagged_archive(tmp_path, monkeypatch, capsys):
    """--force 跳过未打标归档后不得回写指纹，并提示连同 manifest.json 清库。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 0
    docs = await _rows(
        root,
        "SELECT id, source_url FROM knowledge_items "
        "WHERE project_id = ? AND tags LIKE '%signal:docs%'",
    )
    assert docs
    archive_memory_docs(root, [r["id"] for r in docs], clear_tags=True)

    capsys.readouterr()
    assert await run_bootstrap(_args(root, force=True)) == 0
    out = capsys.readouterr().out
    manifest = json.loads((root / ".rsi" / "manifest.json").read_text(encoding="utf-8"))
    stamped = {r["source_url"] for r in docs if r["source_url"] in manifest}
    assert not stamped
    assert "manifest.json" in out
    assert "rsi wipe --yes" in out


def test_readme_wipe_mentions_manifest():
    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "manifest.json" in text
    assert "rsi memory migrate" in text


async def test_bootstrap_does_not_demote_active_auto_extract(tmp_path, monkeypatch):
    """已生效日常稿极性相反时，再次 bootstrap 不得整批降级 auto-extract。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    rt = await build_runtime(project_root=root)
    try:
        await rt.knowledge.add(KnowledgeItem(
            project_id=rt.project_id,
            title="API 用 pydantic",
            content="允许使用 pydantic 校验请求体。" * 4,
            status="active",
            content_type="convention",
            tags=[],
            source_url="auto-extract",
        ))
        await rt.knowledge.add(KnowledgeItem(
            project_id=rt.project_id,
            title="禁止：pydantic",
            content="禁止使用 pydantic 作为入参。" * 4,
            status="active",
            content_type="prohibition",
            tags=[],
            source_url="auto-extract",
        ))
    finally:
        await rt.close()

    assert await run_bootstrap(_args(root)) == 0

    autos = await _rows(
        root,
        "SELECT title, status FROM knowledge_items "
        "WHERE project_id = ? AND source_url = 'auto-extract'",
    )
    assert len(autos) == 2
    assert all(r["status"] == "active" for r in autos)


async def test_dry_run_skips_write_and_gate(tmp_path, monkeypatch):
    """dry-run：不写库、不落 run id。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")

    assert await run_bootstrap(_args(root, dry_run=True)) == 0
    assert not (root / ".rsi" / "bootstrap_run.json").exists()
    assert not (root / ".rsi" / "manifest.json").exists()
    assert not (root / ".rsi" / "rsi.db").exists()


async def test_dry_run_reports_apply_and_confirm_lanes(tmp_path, monkeypatch, capsys):
    """dry-run：文件级将直通/将进确认，不切片、不 gate。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    (root / "CLAUDE.md").write_text("禁止使用 foo 作为默认依赖。\n", encoding="utf-8")

    import rsi_boot.cli.bootstrap_command as cmd

    sliced = {"n": 0}

    def _no_slice(*_a, **_k):
        sliced["n"] += 1
        raise AssertionError("dry-run 不得调用 slice_document")

    monkeypatch.setattr(cmd, "slice_document", _no_slice)

    assert await run_bootstrap(_args(root, dry_run=True)) == 0
    assert sliced["n"] == 0
    out = capsys.readouterr().out
    assert "将直通" in out
    assert "README.md" in out
    assert "将进确认" in out
    assert "CLAUDE.md" in out
