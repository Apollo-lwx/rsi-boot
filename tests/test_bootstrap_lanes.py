"""Task 5：bootstrap 写入车道、run id、审批帽隔离。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

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


def _pack_paths(root: Path) -> set[str]:
    out: set[str] = set()
    packs = root / ".rsi" / "state" / "reading-packs"
    if not packs.is_dir():
        return out
    for path in packs.glob("*.yaml"):
        if path.name == "index.yaml":
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for src in data.get("sources") or []:
            if isinstance(src, dict) and src.get("path"):
                out.add(str(src["path"]).replace("\\", "/"))
    return out


async def test_clean_repo_docs_and_config_are_active(tmp_path, monkeypatch):
    """干净小仓：写阅读包与 run id，不灌配置摘要知识。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")

    assert await run_bootstrap(_args(root)) == 0

    run_path = root / ".rsi" / "bootstrap_run.json"
    assert run_path.is_file()
    run = json.loads(run_path.read_text(encoding="utf-8"))
    rid = run["latest"]
    assert rid and len(rid) == 32
    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["knowledge_written"] == 0
    assert report["pack_count"] >= 1
    assert any(p.endswith("README.md") or p == "README.md" for p in _pack_paths(root))


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
    paths = _pack_paths(root)
    assert any(p.startswith("foo-v1.0.md") for p in paths)
    assert any(p.startswith("foo-v1.1.md") for p in paths)
    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["knowledge_written"] == 0


async def test_incremental_version_sibling_holds_existing_active(tmp_path, monkeypatch):
    """5a：增量出现版本兄弟也不再 hold / 预灌 version 冲突。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    (root / "foo-v1.0.md").write_text(
        "# Foo\n\n" + _long("旧版接口返回 xml 且字段名为 user_id"),
        encoding="utf-8",
    )

    assert await run_bootstrap(_args(root)) == 0
    assert any(p.startswith("foo-v1.0.md") for p in _pack_paths(root))

    (root / "foo-v1.1.md").write_text(
        "# Foo\n\n" + _long("新版接口返回 json 且字段名为 accountId"),
        encoding="utf-8",
    )
    assert await run_bootstrap(_args(root)) == 0
    paths = _pack_paths(root)
    assert any(p.startswith("foo-v1.0.md") for p in paths)
    assert any(p.startswith("foo-v1.1.md") for p in paths)


def _src(row: dict) -> str:
    return (row.get("source_url") or "").replace("\\", "/")


def _multichunk_doc(title: str, note: str) -> str:
    parts = [f"# {title}\n"]
    for i, name in enumerate(("接口", "字段", "错误码"), 1):
        body = (f"{note} 第{i}节{name}说明，旧新口径不同且正文足够长。" * 50)
        parts.append(f"## {name}\n\n{body}\n")
    return "\n".join(parts)


async def _seed_version_knowledge(root: Path) -> None:
    rt = await build_runtime(project_root=root)
    try:
        for rel, title, body in (
            ("foo-v1.0.md", "Foo v1.0", "旧版接口返回 xml 且字段名为 user_id"),
            ("foo-v1.1.md", "Foo v1.1", "新版接口返回 json 且字段名为 accountId"),
        ):
            await rt.knowledge.add(KnowledgeItem(
                project_id=rt.project_id,
                title=title,
                content=_long(body),
                status="active",
                content_type="documentation",
                source_url=rel,
            ))
    finally:
        await rt.close()


async def test_bootstrap_version_keep_peer_activates_kept(tmp_path, monkeypatch):
    """已有短知识 + 版本文件：scan 开冲突后 keep_peer 归档旧版。"""
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
    await _seed_version_knowledge(root)
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
    await _seed_version_knowledge(root)
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
    await _seed_version_knowledge(root)
    assert await run_bootstrap(_args(root)) == 0
    paths = _pack_paths(root)
    assert any(p.startswith("foo-v1.0.md") for p in paths)
    assert any(p.startswith("foo-v1.1.md") for p in paths)
    conflicts = [
        r for r in memory_conflict_rows(root)
        if (r.get("conflict_type") or r.get("type")) == "version"
        and r.get("status", "open") == "open"
    ]
    assert len(conflicts) == 1


async def test_force_does_not_revive_untagged_overflow_archive(tmp_path, monkeypatch):
    """已归档短知识在 --force 采集后仍 archived（不再因文件列表复活）。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    rt = await build_runtime(project_root=root)
    try:
        added = await rt.knowledge.add(KnowledgeItem(
            project_id=rt.project_id,
            title="旧文档切片",
            content=_long("这是一条将被归档的文档知识"),
            status="active",
            content_type="documentation",
            tags=["signal:docs"],
            source_url="README.md",
        ))
        doc_id = added["id"] if isinstance(added, dict) else added
    finally:
        await rt.close()
    archive_memory_docs(root, [doc_id], clear_tags=True)

    assert await run_bootstrap(_args(root, force=True)) == 0
    leftover = await _rows(
        root,
        "SELECT id, status FROM knowledge_items WHERE project_id = ? AND id = ?",
        (doc_id,),
    )
    assert leftover
    assert all(r["id"] == doc_id and r["status"] == "archived" for r in leftover)


async def test_force_rewrites_packs_without_touching_old_yaml(tmp_path, monkeypatch):
    """--force 重写阅读包为 pending，不写 manifest、不灌知识。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 0
    assert await run_bootstrap(_args(root, force=True)) == 0
    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["knowledge_written"] == 0
    assert report["pack_count"] >= 1
    assert not (root / ".rsi" / "manifest.json").exists()


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
    """dry-run：打印将生成的包数/域，不写盘、不切片。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _clean_repo(tmp_path / "proj")
    (root / "CLAUDE.md").write_text("禁止使用 foo 作为默认依赖。\n", encoding="utf-8")

    assert await run_bootstrap(_args(root, dry_run=True)) == 0
    out = capsys.readouterr().out
    assert "将生成阅读包" in out
    assert not (root / ".rsi" / "state" / "reading-packs" / "index.yaml").exists()
