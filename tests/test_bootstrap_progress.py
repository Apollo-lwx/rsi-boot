"""bootstrap 进度：阶段、计数、剩余时间，默认打到 stdout（避免 PowerShell 红块）。"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
from pathlib import Path

import pytest

from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.cli.progress import (
    Progress,
    display_width,
    estimate_remaining,
    format_bar,
    format_duration,
    format_remaining,
)


def test_format_duration():
    assert format_duration(0) == "0秒"
    assert format_duration(5.9) == "5秒"
    assert format_duration(75) == "1分15秒"
    assert format_duration(120) == "2分"
    assert format_duration(3720) == "1小时2分"


def test_estimate_remaining():
    assert estimate_remaining(done=0, total=100, elapsed=10) is None
    assert estimate_remaining(done=25, total=100, elapsed=10) == 30
    assert estimate_remaining(done=100, total=100, elapsed=10) is None


def test_format_remaining_never_says_zero_while_work_left():
    assert format_remaining(0.3) == "不到1秒"
    assert format_remaining(0.8) == "不到1秒"
    assert format_remaining(1.2) == "2秒"
    assert "0秒" not in format_remaining(0.79)


def test_display_width_counts_cjk_double():
    assert display_width("ab") == 2
    assert display_width("冲突") == 4
    assert display_width("[9/11] 冲突") == 11


class _TtyBuf(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_progress_tty_narrow_terminal_does_not_wrap(monkeypatch):
    buf = _TtyBuf()
    monkeypatch.setattr("rsi_boot.cli.progress.terminal_columns", lambda _stream=None: 72)
    p = Progress(stream=buf, min_interval=0)
    p.start(total_phases=11)
    p.phase("冲突检测", total=89988)
    p._elapsed_override = 835.0
    p.tick(89903)
    last = buf.getvalue().split("\r")[-1].rstrip(" ")
    assert display_width(last) <= 71
    assert "约剩 0秒" not in buf.getvalue()
    assert "约剩[9/11]" not in buf.getvalue()
    assert "89903/89988" in last
    assert "[" in last and "]" in last and "%" in last


def test_format_bar():
    assert format_bar(0, 100) == "[                    ] 0%"
    assert format_bar(5, 100) == "[=                   ] 5%"
    assert format_bar(25, 100) == "[=====               ] 25%"
    assert format_bar(100, 100) == "[====================] 100%"
    assert format_bar(3, 0) == "[                    ] 0%"


def test_progress_phase_line_includes_eta():
    buf = io.StringIO()
    p = Progress(stream=buf, min_interval=0)
    p.start(total_phases=4)
    p.phase("文档", total=100)
    p._elapsed_override = 10.0
    p.tick(25)
    text = buf.getvalue()
    assert "[1/4]" in text
    assert "文档" in text
    assert "25/100" in text
    assert "约剩" in text
    assert "[=====               ] 25%" in text


def test_progress_retarget_keeps_phase_index():
    buf = io.StringIO()
    p = Progress(stream=buf, min_interval=0)
    p.start(total_phases=11)
    p.phase("冲突检测", total=10)
    p.tick(10)
    p.retarget("冲突检测（极性配对）")
    p.tick(3)
    text = buf.getvalue()
    assert "[1/11]" in text
    assert "[2/11]" not in text
    assert "极性配对" in text
    assert "3" in text


def test_progress_unknown_total_shows_count_only():
    buf = io.StringIO()
    p = Progress(stream=buf, min_interval=0)
    p.start(total_phases=2)
    p.phase("扫描文件树")
    p.tick(12)
    text = buf.getvalue()
    assert "扫描文件树" in text
    assert "12" in text
    assert "约剩" not in text


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="config", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500, include=[],
        host_judge=False, local_judge=True,
    )
    return argparse.Namespace(**{**defaults, **overrides})


async def test_bootstrap_prints_phase_progress(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_text("# Demo\n\n" + "内容足够长用于切片。" * 20, encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\ndependencies = ["pydantic>=2"]\n', encoding="utf-8")

    assert await run_bootstrap(_args(root, scope="config")) == 0
    out = capsys.readouterr().out
    assert "开始学习" in out
    assert "扫描文件树" in out
    assert "配置" in out
    assert "已用" in out
    assert "冲突检测" in out
    assert "写入知识" in out


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


async def test_bootstrap_git_phase_shows_commit_count(tmp_path, monkeypatch, capsys):
    if shutil.which("git") is None:
        pytest.skip("git 不可用")
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "dev@example.com")
    _git(root, "config", "user.name", "Dev")
    (root / "README.md").write_text("# Demo\n\n" + "内容足够长用于切片。" * 20, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "docs: seed")
    (root / "note.md").write_text("# Note\n\n" + "第二份提交内容。" * 20, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "docs: note")

    assert await run_bootstrap(_args(root, scope="git")) == 0
    out = capsys.readouterr().out
    assert "git 2" in out
    assert "Git（2 次提交）" in out
