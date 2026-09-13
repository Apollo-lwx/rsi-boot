"""Task 6：bootstrap Markdown 学习报告。"""

from __future__ import annotations

import argparse
from pathlib import Path

from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.scanner.report import BootstrapReport

_HEADINGS = ("## 画像", "## 直通生效", "## 切片统计", "## 抽取待确认", "## 冲突组", "## 下一步")
_DIALOG_HINT = "下一次任务会在对话里弹出抉择"


def test_write_markdown_has_six_sections_and_dialog_hint(tmp_path):
    report = BootstrapReport(
        project_root=str(tmp_path),
        profile_summary={"language": "Python", "framework": "FastAPI"},
        applied={"docs": 2, "config": 1},
        applied_samples={"docs": ["README# 使用", "ADR# 背景"]},
        slice_stats={"source_files": 1, "chunks": 3, "merged_tiny": 0},
        extracts=[{"title": "禁止硬编码密钥", "source": ".cursor/rules/x.mdc"}],
        conflicts=[{
            "type": "version",
            "left": "foo-v1.0.md",
            "right": "foo-v1.1.md",
            "reason": "同族多版本",
        }],
    )
    out = tmp_path / "bootstrap_report.md"
    report.write_markdown(out)

    text = out.read_text(encoding="utf-8")
    for heading in _HEADINGS:
        assert heading in text, f"missing {heading}"
    assert _DIALOG_HINT in text


def test_write_markdown_caps_conflict_samples_and_counts(tmp_path):
    report = BootstrapReport(
        project_root=str(tmp_path),
        conflict_counts={"incoherent": 86464, "doc_code": 43, "version": 0},
        conflicts=[
            {"type": "incoherent", "left": f"a{i}.md", "right": f"b{i}.md", "reason": "x"}
            for i in range(80)
        ],
    )
    out = tmp_path / "bootstrap_report.md"
    report.write_markdown(out)
    text = out.read_text(encoding="utf-8")
    assert "incoherent 86464" in text
    assert text.count("**incoherent**") <= 30


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="config", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500,
    )
    return argparse.Namespace(**{**defaults, **overrides})


async def test_bootstrap_writes_markdown_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_text(
        "# Demo\n\n## 使用\n" + ("这是一段足够长的仓库原文。" * 40),
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        '[project]\ndependencies = ["pydantic>=2"]\n', encoding="utf-8"
    )

    assert await run_bootstrap(_args(root)) == 0

    md_path = root / ".rsi" / "bootstrap_report.md"
    assert md_path.is_file()
    text = md_path.read_text(encoding="utf-8")
    for heading in _HEADINGS:
        assert heading in text
    assert _DIALOG_HINT in text

    out = capsys.readouterr().out
    assert "bootstrap_report.md" in out


def test_render_terminal_shows_judge_line(tmp_path):
    report = BootstrapReport(
        project_root=str(tmp_path),
        judge="host", judge_candidates=12, judge_unresolved=12,
        judge_queue_path=str(tmp_path / ".rsi" / "host_judge_queue.json"),
    )
    text = report.render_terminal()
    assert "judge=host" in text
    assert "12" in text
    assert "host_judge_queue.json" in text


def test_render_terminal_shows_omitted_hint(tmp_path):
    report = BootstrapReport(
        project_root=str(tmp_path), judge="local", judge_candidates=10000,
        judge_omitted=320,
    )
    text = report.render_terminal()
    assert "judge=local" in text
    assert "320" in text
    assert "--include" in text
