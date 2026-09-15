# tests/test_git_fix.py
import shutil
import subprocess
from pathlib import Path

import pytest

from rsi_boot.scanner.git_fix import is_fix_subject, list_fix_commits


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_fix_word_not_substring():
    assert is_fix_subject("fix token refresh")
    assert is_fix_subject("fix: npe")
    assert is_fix_subject("hotfix login")
    assert is_fix_subject("revert bad deploy")
    assert is_fix_subject("修复登录空指针")
    assert is_fix_subject("回滚错误发布")
    assert is_fix_subject("缺陷：会话丢失")
    assert not is_fix_subject("prefix the path")
    assert not is_fix_subject("docs: update readme")
    assert not is_fix_subject("chore: bump")


def test_list_fix_commits_filters_subjects(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git 不可用")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("a", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fix: a")
    (repo / "README.md").write_text("# b", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "docs: b")

    fixes = list_fix_commits(repo)
    assert len(fixes) == 1
    assert fixes[0]["message"] == "fix: a"
    assert "src/a.py" in fixes[0]["files"]
    assert "hash" in fixes[0]


def test_list_fix_commits_no_git(tmp_path):
    assert list_fix_commits(tmp_path) == []


def test_list_fix_commits_linked_worktree(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git 不可用")
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("a", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fix: a")
    _git(repo, "worktree", "add", str(worktree))
    assert (worktree / ".git").is_file()
    fixes = list_fix_commits(worktree)
    assert [row["message"] for row in fixes] == ["fix: a"]
