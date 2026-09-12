"""bootstrap 进度：阶段、计数、剩余时间，默认打到 stdout（避免 PowerShell 红块）。"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.cli.progress import Progress, estimate_remaining, format_bar, format_duration


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
        max_file_size="1MB", max_commits=500,
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
