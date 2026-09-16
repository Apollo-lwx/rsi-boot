"""Task 6：bootstrap Markdown 学习报告。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.scanner.report import BootstrapReport

_HEADINGS = ("## 仓库信号", "## 阅读包", "## 蒸馏进度", "## 画像")


def test_write_markdown_has_inventory_not_agent_menu(tmp_path):
    report = BootstrapReport(
        project_root=str(tmp_path),
        judge="host",
        signals={"docs": 12, "code": 40},
        pack_omitted_sources=["docs/extra.md"],
        harvest_warning="库存仍有 2 条旧采集物",
        profile_summary={"language": "Python", "framework": "FastAPI"},
        conflicts=[{
            "type": "version",
            "left": "foo-v1.0.md",
            "right": "foo-v1.1.md",
            "reason": "同族多版本",
        }],
    )
    report.apply_pack_inventory([
        {"id": "docs-0000", "domain": "docs", "status": "pending", "source_count": 8},
        {"id": "src-auth", "domain": "src/auth", "status": "pending", "source_count": 12},
        {"id": "git-0000", "domain": "git-fix", "status": "pending", "source_count": 3},
    ])
    out = tmp_path / "bootstrap_report.md"
    report.write_markdown(out)

    text = out.read_text(encoding="utf-8")
    for heading in _HEADINGS:
        assert heading in text, f"missing {heading}"
    assert "## 下一步" not in text
    assert "选哪一项" not in text
    assert "Which option?" not in text
    assert "pack_list" not in text
    assert "先不蒸馏" not in text
    assert "代码" in text
    assert "docs / README" in text
    assert "git-fix" in text
    assert "docs/extra.md" in text
    assert "库存仍有 2 条旧采集物" in text
    assert "pending 3" in text
    assert "Cursor" not in text
    assert "弹出抉择" not in text
    assert "knowledge accept" not in text
    assert "直通生效" not in text


def test_write_markdown_skip_is_uncovered_not_finished(tmp_path):
    report = BootstrapReport(
        project_root=str(tmp_path),
        signals={"docs": 12, "git": 619},
        git_summary="500 commits, 2 人, conventional 80%",
        judge="host",
    )
    report.apply_pack_inventory(
        [
            {"id": "a", "domain": "docs", "status": "done", "source_count": 2},
            {"id": "b", "domain": "conversation", "status": "skipped", "source_count": 8},
            {"id": "c", "domain": "conversation", "status": "skipped", "source_count": 8},
        ],
        distilled_count=4,
    )
    out = tmp_path / "bootstrap_report.md"
    report.write_markdown(out)
    text = out.read_text(encoding="utf-8")
    assert "已处理完" not in text
    assert "未覆盖" in text
    assert "skipped 2" in text
    assert report.knowledge_written == 4
    assert "- git 提交信号: 619" in text
    assert "- git 摘要:" in text
    assert text.count("- git:") == 0


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
        max_file_size="1MB", max_commits=500, include=[],
        host_judge=False, local_judge=True,
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
    assert "## 下一步" not in text
    assert "pack_list" not in text
    assert "Cursor" not in text
    assert "弹出抉择" not in text

    out = capsys.readouterr().out
    assert "bootstrap_report.md" in out


def test_render_terminal_shows_judge_line(tmp_path):
    report = BootstrapReport(
        project_root=str(tmp_path),
        judge="host",
        pack_count=12,
    )
    text = report.render_terminal()
    assert "judge=host" in text
    assert "12" in text
    assert "host_judge_queue" not in text
    assert "选哪一项" not in text
    assert "pack_list" not in text


def test_render_terminal_shows_local_relearn_line(tmp_path):
    report = BootstrapReport(
        project_root=str(tmp_path), judge="local", pack_count=3,
    )
    text = report.render_terminal()
    assert "judge=local" in text
    assert "3" in text
    assert "rsi-relearn" in text
    assert "当前宿主" in text
    assert "Cursor" not in text
    assert "IDE" not in text


def test_render_terminal_shows_harvest_warning(tmp_path):
    report = BootstrapReport(
        project_root=str(tmp_path),
        harvest_warning="库存仍有 2 条旧采集物",
    )
    text = report.render_terminal()
    assert "库存仍有 2 条旧采集物" in text


def test_write_json_has_pack_fields_not_judge_star(tmp_path):
    report = BootstrapReport(
        project_root=str(tmp_path),
        judge="host",
        pack_count=4,
        pack_omitted_sources=["docs/extra.md"],
    )
    out = tmp_path / "bootstrap_report.json"
    report.write_json(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["pack_count"] == 4
    assert data["pack_omitted_sources"] == ["docs/extra.md"]
    assert data["judge"] == "host"
    assert data["harvest_warning"] == ""
    assert "judge_candidates" not in data
    assert "judge_omitted" not in data
    assert "judge_unresolved" not in data
    assert "judge_queue_path" not in data


def test_phase_names_are_collection_stages_without_conflict():
    from rsi_boot.cli.bootstrap_command import _phase_names

    names = _phase_names(
        {"docs": True, "code": True, "git": True, "config": True, "conversation": True},
        False,
        "zh",
    )
    assert names[0] == "扫描文件树"
    assert "文档索引" in names
    assert "代码索引" in names
    assert "Git fix" in names
    assert "规则与技能" in names
    assert "分包" in names
    assert "冲突检测" not in names
