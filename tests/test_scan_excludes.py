"""默认排除噪声目录；--include 把指定目录加回学习。"""

from __future__ import annotations

from pathlib import Path

from rsi_boot.__main__ import build_parser
from rsi_boot.scanner.signal_discovery import (
    discover_signals,
    parse_include_dirs,
    source_path_excluded,
)


def _noise_tree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("# Demo\n\nhello\n", encoding="utf-8")
    for folder, name in (
        (".worktrees/copy", "dup.md"),
        (".auto-learn/gap", "audit.md"),
        (".superpowers/sdd", "task.md"),
        ("artifacts/run1", "out.md"),
        ("datasets/artifacts/pipe", "extract.md"),
        ("docs", "guide.md"),
    ):
        dest = root / folder
        dest.mkdir(parents=True)
        (dest / name).write_text("# x\n\nbody\n", encoding="utf-8")
    return root


def test_parse_include_dirs_splits_and_strips():
    assert parse_include_dirs([".auto-learn", "docs/extra, artifacts"]) == [
        ".auto-learn", "docs/extra", "artifacts",
    ]


def test_parse_include_dirs_strips_dot_slash_and_dedupes_case():
    assert parse_include_dirs(["./.auto-learn", ".Auto-Learn"]) == [".auto-learn"]


def test_discover_skips_noise_dirs_by_default(tmp_path: Path):
    root = _noise_tree(tmp_path)
    signals = discover_signals(root)
    paths = {p.relative_to(root).as_posix() for p in signals["docs"].files}
    assert "README.md" in paths
    assert "docs/guide.md" in paths
    assert not any(p.startswith(".worktrees/") for p in paths)
    assert not any(p.startswith(".auto-learn/") for p in paths)
    assert not any(p.startswith(".superpowers/") for p in paths)
    assert not any("/artifacts/" in p or p.startswith("artifacts/") for p in paths)


def test_discover_include_auto_learn(tmp_path: Path):
    root = _noise_tree(tmp_path)
    signals = discover_signals(root, include=[".auto-learn"])
    paths = {p.relative_to(root).as_posix() for p in signals["docs"].files}
    assert any(p.startswith(".auto-learn/") for p in paths)
    assert not any(p.startswith(".worktrees/") for p in paths)


def test_cli_include_flag():
    args = build_parser().parse_args([
        "bootstrap", "--include", ".auto-learn", "--include", "artifacts",
    ])
    assert args.include == [".auto-learn", "artifacts"]


def test_include_nested_does_not_open_siblings(tmp_path: Path):
    root = _noise_tree(tmp_path)
    other = root / ".worktrees" / "other"
    other.mkdir()
    (other / "skip.md").write_text("# o\n\nbody\n", encoding="utf-8")
    signals = discover_signals(root, include=[".worktrees/copy"])
    paths = {p.relative_to(root).as_posix() for p in signals["docs"].files}
    assert any(p.startswith(".worktrees/copy/") for p in paths)
    assert not any(p.startswith(".worktrees/other/") for p in paths)


def test_include_dot_slash_reopens_excluded_dir(tmp_path: Path):
    root = _noise_tree(tmp_path)
    signals = discover_signals(root, include=["./.auto-learn"])
    paths = {p.relative_to(root).as_posix() for p in signals["docs"].files}
    assert any(p.startswith(".auto-learn/") for p in paths)


def test_source_path_excluded_filters_leftover_noise():
    assert source_path_excluded(".auto-learn/gap/audit.md", [])
    assert not source_path_excluded(".auto-learn/gap/audit.md", [".auto-learn"])
    assert not source_path_excluded("docs/guide.md", [])
    assert source_path_excluded(".worktrees/other/skip.md", [".worktrees/copy"])
    assert not source_path_excluded(".worktrees/copy/dup.md", [".worktrees/copy"])
