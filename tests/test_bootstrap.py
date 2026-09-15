"""P1.17 bootstrap 核心扫描层测试。"""

import argparse
import json
from pathlib import Path

from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.scanner.config_scanner import scan_configs
from rsi_boot.scanner.document_scanner import slice_document
from rsi_boot.scanner.profile_generator import assess_test_culture
from rsi_boot.scanner.signal_discovery import discover_signals, plan_scopes
from rsi_boot.scanner.validator import DedupSet, validate_chunk


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text(
        "# Demo\n\n## 安装\npip install demo\n\n## 使用\ndemo run\n\n## 配置\n编辑 config.yaml\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(3):
        (docs / f"guide{i}.md").write_text(
            f"# 指南 {i}\n\n" + "这是一段足够长的文档内容，" * 20, encoding="utf-8"
        )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["pydantic>=2", "pytest>=8"]\n'
        '[build-system]\nbuild-backend = "hatchling.build"\n',
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hi')\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text("def test_x(): pass\n", encoding="utf-8")
    return tmp_path


def _args(tmp_path: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(tmp_path), scope="", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500, include=[],
        host_judge=False, local_judge=True,
    )
    return argparse.Namespace(**{**defaults, **overrides})


def test_bootstrap_source_has_no_init_db_phrase():
    src = Path(__file__).resolve().parents[1] / "src" / "rsi_boot" / "cli" / "bootstrap_command.py"
    text = src.read_text(encoding="utf-8")
    assert "初始化数据库" not in text
    assert "数据库" not in text
    assert 't("BOOTSTRAP_PHASE_INIT"' in text


def test_signal_discovery(tmp_path):
    root = _make_project(tmp_path)
    signals = discover_signals(root)
    assert signals["docs"].present and signals["docs"].file_count >= 4
    assert signals["config"].present
    assert signals["tests"].present
    assert not signals["git"].present


def test_signal_summary_shows_git_commit_count():
    from rsi_boot.cli.bootstrap_command import _signal_summary
    from rsi_boot.scanner.signal_discovery import SignalInfo

    git = SignalInfo("git", True, item_count=134)
    assert _signal_summary({"git": git}) == "git 134"


def test_plan_scopes_consent_gate(tmp_path):
    signals = discover_signals(_make_project(tmp_path))
    plan = plan_scopes(signals, "", consent=False)
    assert plan["conversation"] is False
    plan_consented = plan_scopes(signals, "conversation", consent=True)
    assert plan_consented["conversation"] is True
    assert plan_consented["docs"] is False  # 指定 scope 时其余维度关闭


def test_slice_document_by_headings(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text(
        "# 标题一\n\n" + "内容一。" * 100 + "\n\n## 标题二\n\n" + "内容二。" * 100,
        encoding="utf-8",
    )
    chunks = slice_document(doc)
    assert [c.title for c in chunks] == ["标题一", "标题二"]


def test_validate_chunk_rules():
    assert not validate_chunk("太短").ok  # 信息量不足
    sensitive = "密钥 sk-" + "a" * 24 + "。" + "这段文字需要足够长才能通过信息量检查，" * 15
    assert not validate_chunk(sensitive).ok  # 敏感默认跳过
    exempted = validate_chunk(sensitive, allow_sensitive=True)
    assert exempted.ok and exempted.risk and "sk-****" in exempted.content


def test_dedup_set():
    dedup = DedupSet()
    assert not dedup.is_duplicate("内容甲")
    assert dedup.is_duplicate("内容甲")


def test_config_scanner_pyproject(tmp_path):
    root = _make_project(tmp_path)
    signals = discover_signals(root)
    insights = scan_configs(
        root, signals["config"].files, signals["conventions"].files,
        signals["ci"].files, signals["code"].files,
    )
    assert insights.language == "python"
    assert insights.test_framework == "pytest"
    assert insights.build_system == "hatchling"


def test_assess_test_culture():
    assert assess_test_culture([Path("a")] * 3, [Path("b")] * 10) == "strong"
    assert assess_test_culture([Path("a")] * 2, [Path("b")] * 10) == "moderate"
    assert assess_test_culture([], [Path("b")] * 10) == "weak"


async def test_bootstrap_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_project(tmp_path)

    # dry-run：无写入
    assert await run_bootstrap(_args(root, dry_run=True)) == 0
    assert not (root / ".rsi" / "manifest.json").exists()

    # 正式执行：只写阅读包，不灌原文知识
    assert await run_bootstrap(_args(root)) == 0
    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["knowledge_written"] == 0
    assert report["pack_count"] >= 1
    assert report["profile_summary"]["language"] == "python"
    assert report["profile_summary"]["test_culture"] == "strong"  # 1 test / 1 code = 100%
    assert (root / ".rsi" / "state" / "reading-packs" / "index.yaml").is_file()

    # 重跑仍不写知识
    assert await run_bootstrap(_args(root)) == 0
    report2 = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report2["knowledge_written"] == 0

    # .gitignore 自动写入
    assert ".rsi" in (root / ".gitignore").read_text(encoding="utf-8")


async def test_bootstrap_pack_write_failure_exits_1(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_project(tmp_path)

    import rsi_boot.cli.bootstrap_command as cmd

    def _explode(*_a, **_k):
        raise OSError("模拟写包失败")

    monkeypatch.setattr(cmd, "write_packs", _explode)
    assert await run_bootstrap(_args(root)) == 1
