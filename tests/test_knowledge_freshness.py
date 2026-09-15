"""知识保鲜（§10.9.10「同一信号已变」）：需求推翻重建/分支岔路场景。

实仓场景缺口：bootstrap 只归档「源文件删除」的 active 条目——
1. 文档推翻重写后重跑，旧切片条目滞留 active/pending_review（新旧混杂）；
2. revert/分支切回时旧内容因 archived 参与去重而无法复活；
3. 源文件删除只归档 active，pending_review 滞留；
4. 聚合类生成知识（配置摘要/Git 摘要/代码骨架/规则种子）内容随开发演进，
   旧版本同样滞留。
"""

import argparse
import json
from pathlib import Path

from rsi_boot.bootstrap import build_runtime
from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.core.models import KnowledgeItem
from rsi_boot.scanner.incremental import IncrementalLearner

from memory_helpers import memory_item_rows


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="docs", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500, include=[],
        host_judge=False, local_judge=True,
    )
    return argparse.Namespace(**{**defaults, **overrides})


async def _proj_items(root: Path):
    return [r for r in memory_item_rows(root) if r.get("content_type") != "skill"]


_LOGIN = "用户通过邮箱验证码登录，验证码有效期十分钟，连续失败五次锁定账户。" * 15
_PAY_V1 = "接入微信支付渠道，下单后两小时内支付有效，超时自动关单不再重开。" * 15
_PAY_V2 = "需求推翻：改为接入支付宝渠道，下单三十分钟内支付有效必须关单。" * 15


def _doc(pay_section: str) -> str:
    return (
        "# 需求文档\n\n"
        f"## 登录\n\n{_LOGIN}\n\n"
        f"## 支付\n\n{pay_section}\n"
    )


def _make_doc_project(root: Path) -> Path:
    root.mkdir()
    (root / "requirements.md").write_text(_doc(_PAY_V1), encoding="utf-8")
    return root


def _report(root: Path) -> dict:
    return json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))


# ---------- 文档变更收敛 ----------


async def test_full_rewrite_does_not_ingest_or_archive_yaml(tmp_path, monkeypatch):
    """文档推翻重写后 bootstrap 只更新阅读包，不灌切片、不归档旧 YAML。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")

    assert await run_bootstrap(_args(root)) == 0
    assert await _proj_items(root) == []
    assert _report(root)["knowledge_written"] == 0
    assert _report(root)["pack_count"] >= 1

    (root / "requirements.md").write_text(_doc(_PAY_V2), encoding="utf-8")
    assert await run_bootstrap(_args(root)) == 0
    assert await _proj_items(root) == []
    assert _report(root)["knowledge_written"] == 0
    assert (root / ".rsi" / "state" / "reading-packs" / "index.yaml").is_file()


async def test_partial_edit_keeps_existing_short_knowledge(tmp_path, monkeypatch):
    """部分改文档：预置短知识保持不动。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")
    rt = await build_runtime(project_root=root)
    try:
        added = await rt.knowledge.add(KnowledgeItem(
            project_id=rt.project_id,
            title="登录",
            content=_LOGIN,
            status="active",
            content_type="documentation",
            source_url="requirements.md",
        ))
        item_id = added["id"] if isinstance(added, dict) else added
    finally:
        await rt.close()

    (root / "requirements.md").write_text(_doc(_PAY_V2), encoding="utf-8")
    assert await run_bootstrap(_args(root)) == 0
    after = await _proj_items(root)
    kept = next(i for i in after if i["id"] == item_id)
    assert kept["status"] == "active"


async def test_deleted_file_does_not_archive_existing_yaml(tmp_path, monkeypatch):
    """源文件删除：bootstrap 不再因文件列表归档旧 YAML。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")
    rt = await build_runtime(project_root=root)
    try:
        added = await rt.knowledge.add(KnowledgeItem(
            project_id=rt.project_id,
            title="需求文档",
            content=_LOGIN,
            status="active",
            content_type="documentation",
            source_url="requirements.md",
        ))
        item_id = added["id"] if isinstance(added, dict) else added
    finally:
        await rt.close()

    (root / "requirements.md").unlink()
    assert await run_bootstrap(_args(root)) == 0
    after = await _proj_items(root)
    kept = next(i for i in after if i["id"] == item_id)
    assert kept["status"] == "active"


# ---------- watch 静默学习收敛 ----------


async def test_watch_relearn_converges_old_chunks(tmp_path, monkeypatch):
    """watch 增量重学同样收敛旧版本（此前注释承认「由下次全量 bootstrap 收敛」）"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")
    runtime = await build_runtime(project_root=root)
    try:
        learner = IncrementalLearner(runtime, root, runtime.project_id)
        doc = root / "requirements.md"
        await learner._relearn(doc)
        before = await _proj_items(root)
        assert before and all(i["status"] == "active" for i in before)

        doc.write_text(_doc(_PAY_V2), encoding="utf-8")
        await learner._relearn(doc)
        after = await _proj_items(root)
        old_ids = {i["id"] for i in before}
        pay_old = [i for i in after if i["id"] in old_ids and "支付" in i["title"]]
        assert pay_old and all(i["status"] == "archived" for i in pay_old)
        login = [i for i in after if i["id"] in old_ids and "登录" in i["title"]]
        assert login and all(i["status"] == "active" for i in login)  # 未变章节保留
    finally:
        await runtime.close()


# ---------- 聚合类生成知识 churn 收敛 ----------


async def test_config_scope_writes_packs_not_summary(tmp_path, monkeypatch):
    """--scope=config 只分包，不写配置摘要知识。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_text("# Demo\n\n## 使用\ndemo run\n", encoding="utf-8")
    pyproject = root / "pyproject.toml"
    pyproject.write_text('[project]\ndependencies = ["pydantic>=2"]\n', encoding="utf-8")

    assert await run_bootstrap(_args(root, scope="config")) == 0
    assert [i for i in await _proj_items(root) if "signal:config" in (i["tags"] or "")] == []
    assert _report(root)["knowledge_written"] == 0
    assert _report(root)["pack_count"] >= 0


async def test_rule_file_lands_in_pack_not_seed_yaml(tmp_path, monkeypatch):
    """用户规则进阅读包，不写 prohibition 种子。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = tmp_path / "proj"
    rules = root / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\ndependencies = ["pydantic>=2"]\n', encoding="utf-8")
    (rules / "a.mdc").write_text("- 禁止使用裸 SQL，一律参数化查询\n", encoding="utf-8")

    assert await run_bootstrap(_args(root, scope="config")) == 0
    assert [i for i in await _proj_items(root) if i["content_type"] == "prohibition"] == []
    text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (root / ".rsi" / "state" / "reading-packs").glob("*.yaml")
    )
    assert "a.mdc" in text
