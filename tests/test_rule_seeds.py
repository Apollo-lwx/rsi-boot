"""ISSUE-6：禁止项冷启动——bootstrap 扫描用户规则文件提取禁止句式，产出 prohibition 草稿。

种子来源：.cursor/rules/*.mdc（非 rsi- 前缀）、.cursorrules、AGENTS.md（托管块除外）。
与 §4.9 冲突检测互补：种子来自用户手写规则，方向天然一致。
"""

import argparse
import json
from pathlib import Path

import pytest

from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.injector.targets import AgentsMdTarget

from memory_helpers import memory_item_rows
from rsi_boot.scanner.rule_seed_scanner import extract_prohibition_lines, scan_rule_seeds


# ---------- 句式提取单元 ----------


def test_extract_prohibition_lines():
    text = """# Java 评审清单

- 禁止直调 Mapper，必须经 Service 层
- 不要在中途修改入参
- 建议使用 Optional 包装返回值
- Never use Executors.* factory methods directly
1. 严禁在循环里发起 RPC 调用
短禁止
"""
    items = extract_prohibition_lines(text)
    assert any("禁止直调 Mapper" in i for i in items)
    assert any("不要在中途修改入参" in i for i in items)
    assert any("严禁在循环里发起 RPC" in i for i in items)
    assert any("Never use Executors" in i for i in items)
    # 许可句式与过短行不提取
    assert not any("Optional" in i for i in items)
    assert not any(i == "短禁止" for i in items)


def test_extract_dedup_within_file():
    text = "- 禁止使用裸 SQL\n- 禁止使用裸 SQL\n"
    items = extract_prohibition_lines(text)
    assert len(items) == 1


# ---------- 文件收集 ----------


def test_scan_rule_seeds_scope(tmp_path):
    """rsi-*.mdc（自产）与 AGENTS.md 托管块不参与种子提取"""
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "java.mdc").write_text("- 禁止字段注入，一律构造器注入\n", encoding="utf-8")
    (rules / "rsi-prohibition-abcd1234.mdc").write_text("- 禁止裸 SQL\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        f"手写说明\n\n{AgentsMdTarget.BEGIN}\n- 禁止：托管块内自产内容\n{AgentsMdTarget.END}\n",
        encoding="utf-8",
    )
    seeds = scan_rule_seeds(tmp_path)
    contents = [s.content for s in seeds]
    assert any("构造器注入" in c for c in contents)
    assert not any("裸 SQL" in c for c in contents)          # rsi- 自产文件排除
    assert not any("托管块内" in c for c in contents)         # 托管块排除
    assert all(s.source.startswith(".cursor/rules/java.mdc#L") for s in seeds)


def test_scan_rule_seeds_no_rules_dir(tmp_path):
    assert scan_rule_seeds(tmp_path) == []


def test_scan_rule_seeds_claude_md_and_copilot(tmp_path):
    """CLAUDE.md 与 .github/copilot-instructions.md 纳入种子来源（实仓观察：
    Claude Code / Copilot 用户的规则载体此前被遗漏）"""
    (tmp_path / "CLAUDE.md").write_text(
        "# 项目说明\n\n- 禁止在测试里连真实数据库\n- 建议使用内存库\n", encoding="utf-8"
    )
    gh = tmp_path / ".github"
    gh.mkdir()
    (gh / "copilot-instructions.md").write_text(
        "- Never commit secrets to the repository\n", encoding="utf-8"
    )
    seeds = scan_rule_seeds(tmp_path)
    by_source: dict[str, list[str]] = {}
    for s in seeds:
        by_source.setdefault(s.source.split("#")[0], []).append(s.content)
    assert any("真实数据库" in c for c in by_source.get("CLAUDE.md", []))
    assert not any("内存库" in c for c in by_source.get("CLAUDE.md", []))  # 许可句不提取
    assert any("commit secrets" in c for c in by_source.get(".github/copilot-instructions.md", []))


# ---------- bootstrap 端到端 ----------


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500, include=[],
        host_judge=False, local_judge=True,
    )
    return argparse.Namespace(**{**defaults, **overrides})


async def _prohibition_rows(root: Path, project_id: str = ""):
    del project_id
    return [r for r in memory_item_rows(root) if r["content_type"] == "prohibition"]


async def test_bootstrap_produces_prohibition_drafts(tmp_path, monkeypatch):
    """含手写禁止规则的仓库 bootstrap 后，审批队列出现 prohibition 草稿"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = tmp_path / "proj"
    rules = root / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "java.mdc").write_text(
        "# 清单\n\n- 禁止直调 Mapper，必须经 Service 层\n- 禁止使用 Executors 直接创建线程池\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Demo\n\n## 使用\ndemo run\n", encoding="utf-8")

    assert await run_bootstrap(_args(root)) == 0
    rows = await _prohibition_rows(root)
    assert len(rows) == 2
    assert all(r["status"] == "pending_review" for r in rows)
    assert any("Mapper" in r["title"] for r in rows)
    assert all(".cursor/rules/java.mdc#L" in r["source_url"] for r in rows)

    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["prohibition_seeds"] == 2


async def test_bootstrap_prohibition_seeds_idempotent(tmp_path, monkeypatch):
    """重跑不产生重复种子（内容指纹去重）"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = tmp_path / "proj"
    rules = root / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "java.mdc").write_text("- 禁止直调 Mapper，必须经 Service 层\n", encoding="utf-8")

    assert await run_bootstrap(_args(root)) == 0
    assert await run_bootstrap(_args(root, force=True)) == 0
    rows = await _prohibition_rows(root)
    assert len(rows) == 1
