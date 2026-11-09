"""迁移闭环：细粒度 gitignore、doctor 检查、bundle 往返、clone 一致性。

验收口径：新仓库学一次 → 提交 → 另一目录 clone → 记忆文件逐位一致。
召回一致性以文件树哈希逐位相同为代理（同一份字节 = 同一份召回输入）。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from rsi_boot.cli.bootstrap_command import ensure_gitignore
from rsi_boot.cli.bundle_command import export_bundle, import_bundle
from rsi_boot.cli.doctor_command import check_memory_tracking

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git 不可用"
)


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"git {args}: {proc.stderr}"
    return proc.stdout


def _commit_all(root: Path) -> None:
    _git(root, "add", "-A")
    _git(
        root,
        "-c", "user.email=test@example.com",
        "-c", "user.name=test",
        "commit", "-m", "init",
    )


def _tree_hashes(root: Path) -> dict[str, str]:
    base = root / ".rsi" / "memory"
    out: dict[str, str] = {}
    for path in sorted(base.rglob("*")):
        if path.is_file():
            rel = path.relative_to(base).as_posix()
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _make_memory(root: Path) -> None:
    memory = root / ".rsi" / "memory"
    (memory / "conventions").mkdir(parents=True)
    (memory / "pending").mkdir(parents=True)
    (memory / "conventions" / "rule-a.yaml").write_text(
        "title: 规则A\ncontent: 测试\n", encoding="utf-8",
    )
    (memory / "pending" / "cand-b.yaml").write_text(
        "title: 候选B\n", encoding="utf-8",
    )
    # 本地产物：不应入库
    (root / ".rsi" / "cache").mkdir(parents=True)
    (root / ".rsi" / "cache" / "inverted.json").write_text("{}", encoding="utf-8")


# ---------- ensure_gitignore ----------


def test_gitignore_fresh_repo(tmp_path: Path) -> None:
    assert ensure_gitignore(tmp_path) is True
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".rsi/*" in text
    assert "!.rsi/memory/" in text
    assert "\n.rsi/\n" not in text


def test_gitignore_migrates_legacy_blanket_rule(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        "# Python\n__pycache__/\n\n# RSI Boot 本地数据\n.rsi/\n.workbuddy/\n",
        encoding="utf-8",
    )
    assert ensure_gitignore(tmp_path) is True
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".rsi/*" in text
    assert "!.rsi/memory/" in text
    assert "__pycache__/" in text  # 无关规则保留
    assert ".workbuddy/" in text
    assert not any(line.strip() == ".rsi/" for line in text.splitlines())


def test_gitignore_idempotent(tmp_path: Path) -> None:
    ensure_gitignore(tmp_path)
    before = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ensure_gitignore(tmp_path) is False
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == before


def test_gitignore_respects_custom_rules(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".rsi/logs/\n", encoding="utf-8")
    assert ensure_gitignore(tmp_path) is False
    assert "!.rsi/memory" not in (tmp_path / ".gitignore").read_text(encoding="utf-8")


# ---------- doctor ----------


def test_doctor_flags_legacy_ignore_then_fix(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _make_memory(tmp_path)
    (tmp_path / ".gitignore").write_text(".rsi/\n", encoding="utf-8")

    report = check_memory_tracking(tmp_path)
    assert report["git_repo"] and report["ignored"] and not report["ok"]
    assert any("忽略" in p for p in report["problems"])

    assert ensure_gitignore(tmp_path) is True
    report = check_memory_tracking(tmp_path)
    assert report["ignored"] is False
    assert not report["ok"]  # 未提交仍有风险

    _commit_all(tmp_path)
    report = check_memory_tracking(tmp_path)
    assert report["ok"], report
    assert report["tracked_files"] == 2  # cache/ 不入库
    # cache 仍然被忽略
    proc = subprocess.run(
        ["git", "check-ignore", "-q", ".rsi/cache/inverted.json"],
        cwd=str(tmp_path), capture_output=True,
    )
    assert proc.returncode == 0


def test_doctor_non_git_repo(tmp_path: Path) -> None:
    _make_memory(tmp_path)
    report = check_memory_tracking(tmp_path)
    assert not report["git_repo"] and not report["ok"]
    assert any("git" in h for h in report["hints"])


# ---------- bundle ----------


def test_bundle_roundtrip_bit_identical(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _make_memory(src)
    out = tmp_path / "bundle"
    assert export_bundle(src, out) == 0
    assert (out / "manifest.json").is_file()

    dst = tmp_path / "dst"
    dst.mkdir()
    assert import_bundle(dst, out) == 0
    assert _tree_hashes(src) == _tree_hashes(dst)


def test_bundle_import_replace_backs_up(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _make_memory(src)
    out = tmp_path / "bundle"
    export_bundle(src, out)

    dst = tmp_path / "dst"
    dst.mkdir()
    import_bundle(dst, out)
    stale = dst / ".rsi" / "memory" / "stale.yaml"
    stale.write_text("old", encoding="utf-8")
    assert import_bundle(dst, out, replace=True) == 0
    assert not stale.exists()
    backups = list((dst / ".rsi").glob("memory.bak-*"))
    assert len(backups) == 1 and (backups[0] / "stale.yaml").is_file()


# ---------- clone 一致性（验收） ----------


def test_clone_preserves_memory_bit_identical(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init")
    _make_memory(origin)
    ensure_gitignore(origin)
    _commit_all(origin)

    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), str(clone))

    # 文件树逐位一致（Jaccard=1.0 的字节级强化版）
    assert _tree_hashes(origin) == _tree_hashes(clone) != {}
    # 克隆里 doctor 全绿；cache 没跟过来
    report = check_memory_tracking(clone)
    assert report["ok"], report
    assert not (clone / ".rsi" / "cache").exists()
