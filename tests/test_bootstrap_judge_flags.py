"""bootstrap 判断旗标 + 阅读包（Task 5b）。"""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

import yaml

from rsi_boot.cli.bootstrap_command import run_bootstrap

_HARVEST_TITLES = {
    "代码骨架摘要",
    "项目配置与规范摘要",
    "Git 历史分析",
    "跨信号关联图谱",
}


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500, include=[],
        host_judge=False, local_judge=False,
    )
    return argparse.Namespace(**{**defaults, **overrides})


def _proj(root: Path) -> Path:
    root.mkdir(exist_ok=True)
    (root / "README.md").write_text(
        "# Demo\n\n## 使用\n" + "这是一段足够长的仓库原文。" * 40,
        encoding="utf-8",
    )
    return root


def _memory_titles(root: Path) -> list[str]:
    mem = root / ".rsi" / "memory"
    if not mem.is_dir():
        return []
    titles: list[str] = []
    for path in mem.rglob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and data.get("title"):
            titles.append(str(data["title"]))
    return titles


def _index(root: Path) -> dict:
    path = root / ".rsi" / "state" / "reading-packs" / "index.yaml"
    assert path.is_file()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert isinstance(data.get("packs"), list)
    return data


def _pack_kinds(root: Path) -> set[str]:
    kinds: set[str] = set()
    for path in (root / ".rsi" / "state" / "reading-packs").glob("*.yaml"):
        if path.name == "index.yaml":
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for src in data.get("sources") or []:
            if isinstance(src, dict) and src.get("kind"):
                kinds.add(str(src["kind"]))
    return kinds


async def test_bootstrap_without_judge_flag_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 2
    assert "必须指定 --host-judge 或 --local-judge" in capsys.readouterr().err


async def test_bootstrap_with_both_judge_flags_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    assert await run_bootstrap(_args(root, host_judge=True, local_judge=True)) == 2
    assert "不能同时使用 --host-judge 与 --local-judge" in capsys.readouterr().err


async def test_bootstrap_dry_run_exempt_from_judge_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    assert await run_bootstrap(_args(root, dry_run=True)) == 0
    assert not (root / ".rsi" / "manifest.json").exists()
    assert not (root / ".rsi" / "state" / "reading-packs" / "index.yaml").exists()


def test_parser_accepts_judge_flags():
    from rsi_boot.__main__ import build_parser
    parser = build_parser()
    args = parser.parse_args(["bootstrap", "--host-judge"])
    assert args.host_judge is True and args.local_judge is False
    args = parser.parse_args(["bootstrap", "--local-judge"])
    assert args.local_judge is True and args.host_judge is False


async def test_host_judge_writes_packs_not_queue_or_harvest(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "a.md").write_text(
        "# 部署\n\n" + "允许使用 docker compose 启动全部服务。" * 8, encoding="utf-8")
    (root / "docs" / "b.md").write_text(
        "# 部署\n\n" + "禁止使用 docker compose 启动全部服务，只起基础镜像。" * 8,
        encoding="utf-8")
    assert await run_bootstrap(_args(root, host_judge=True)) == 0
    assert not (root / ".rsi" / "host_judge_queue.json").exists()
    index = _index(root)
    assert index["packs"]
    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["judge"] == "host"
    assert report["knowledge_written"] == 0
    assert report["pack_count"] >= 1
    assert "judge_queue_path" not in report
    assert not (_HARVEST_TITLES & set(_memory_titles(root)))


async def test_local_judge_writes_packs_without_distilled_knowledge(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    assert await run_bootstrap(_args(root, local_judge=True)) == 0
    assert not (root / ".rsi" / "host_judge_queue.json").exists()
    index = _index(root)
    assert index["packs"]
    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["judge"] == "local"
    assert report["knowledge_written"] == 0
    titles = set(_memory_titles(root))
    assert not (_HARVEST_TITLES & titles)


async def test_force_rewrites_done_packs_to_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    packs = root / ".rsi" / "state" / "reading-packs"
    packs.mkdir(parents=True)
    (packs / "index.yaml").write_text(
        "bootstrap_run_id: old\n"
        "packs:\n"
        "  - id: README-0\n"
        "    domain: README\n"
        "    title: old\n"
        "    status: done\n"
        "    source_count: 1\n"
        "    fingerprint: deadbeef\n",
        encoding="utf-8",
    )
    (packs / "README-0.yaml").write_text(
        "id: README-0\nstatus: done\nsources: []\nfingerprint: deadbeef\n",
        encoding="utf-8",
    )
    assert await run_bootstrap(_args(root, host_judge=True, force=True)) == 0
    index = _index(root)
    assert index["packs"]
    assert all(p.get("status") == "pending" for p in index["packs"])


async def test_chinese_fix_commit_lands_in_git_fix_pack(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        import pytest
        pytest.skip("git 不可用")
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _proj(tmp_path / "proj")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "修复 token 刷新"],
        cwd=root, check=True, capture_output=True,
    )
    assert await run_bootstrap(_args(root, host_judge=True)) == 0
    assert "git_fix" in _pack_kinds(root)
