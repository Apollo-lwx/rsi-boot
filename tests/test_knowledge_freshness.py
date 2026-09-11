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

from rsi_boot.bootstrap import build_runtime, default_db_path
from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.data.sqlite import SQLiteClient
from rsi_boot.scanner.incremental import IncrementalLearner


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="docs", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500,
    )
    return argparse.Namespace(**{**defaults, **overrides})


async def _items(db_path: Path, project_id: str):
    db = SQLiteClient(db_path)
    try:
        conn = await db.connect()
        async with conn.execute(
            "SELECT id, title, content, content_type, status, source_url, tags "
            "FROM knowledge_items WHERE project_id = ? ORDER BY created_at, id",
            (project_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


_LOGIN = "用户通过邮箱验证码登录，验证码有效期十分钟，连续失败五次锁定。" * 10
_PAY_V1 = "接入微信支付渠道，下单后两小时内支付有效，超时自动关单。" * 10
_PAY_V2 = "需求推翻：改为接入支付宝渠道，下单三十分钟内支付有效。" * 10


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


async def test_full_rewrite_supersedes_old_chunks(tmp_path, monkeypatch):
    """需求整体推翻重写 → 重跑后旧切片全部 archived，新切片 pending_review"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")

    assert await run_bootstrap(_args(root)) == 0
    before = await _items(default_db_path(), root.name)
    assert before and all(i["status"] == "pending_review" for i in before)

    (root / "requirements.md").write_text(_doc(_PAY_V2), encoding="utf-8")
    assert await run_bootstrap(_args(root)) == 0
    after = await _items(default_db_path(), root.name)

    old_ids = {i["id"] for i in before}
    pay_old = [i for i in after if i["id"] in old_ids and "支付" in i["title"]]
    assert pay_old and all(i["status"] == "archived" for i in pay_old)  # 旧版本收敛
    pay_new = [i for i in after if i["id"] not in old_ids and "支付" in i["title"]]
    assert pay_new and all(i["status"] == "pending_review" for i in pay_new)
    assert _report(root)["superseded"] >= 1


async def test_partial_edit_keeps_unchanged_sections(tmp_path, monkeypatch):
    """部分修改：未变更章节的条目原样保留（同 id、状态不动）"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 0
    before = await _items(default_db_path(), root.name)
    login_before = next(i for i in before if "登录" in i["title"])

    (root / "requirements.md").write_text(_doc(_PAY_V2), encoding="utf-8")
    assert await run_bootstrap(_args(root)) == 0
    after = await _items(default_db_path(), root.name)

    login_after = next(i for i in after if i["id"] == login_before["id"])
    assert login_after["status"] == "pending_review"  # 未变章节不收敛、不重建


async def test_revert_revives_archived_items(tmp_path, monkeypatch):
    """revert/分支切回：旧版本内容回来时复活（同 id 回 pending_review，不产生重复行）"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 0
    v1_items = await _items(default_db_path(), root.name)
    v1_pay = next(i for i in v1_items if "支付" in i["title"])

    (root / "requirements.md").write_text(_doc(_PAY_V2), encoding="utf-8")
    assert await run_bootstrap(_args(root)) == 0
    # 切回 v1（revert）
    (root / "requirements.md").write_text(_doc(_PAY_V1), encoding="utf-8")
    assert await run_bootstrap(_args(root)) == 0
    after = await _items(default_db_path(), root.name)

    revived = next(i for i in after if i["id"] == v1_pay["id"])
    assert revived["status"] == "pending_review"
    # 同内容不产生重复行
    assert sum(1 for i in after if i["content"] == v1_pay["content"]) == 1
    assert _report(root)["revived"] >= 1


async def test_deleted_file_archives_pending_review_too(tmp_path, monkeypatch):
    """源文件删除：pending_review 条目一并归档（此前只处理 active）"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 0

    (root / "requirements.md").unlink()
    assert await run_bootstrap(_args(root)) == 0
    after = await _items(default_db_path(), root.name)
    assert after and all(i["status"] == "archived" for i in after)


# ---------- watch 静默学习收敛 ----------


async def test_watch_relearn_converges_old_chunks(tmp_path, monkeypatch):
    """watch 增量重学同样收敛旧版本（此前注释承认「由下次全量 bootstrap 收敛」）"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")
    runtime = await build_runtime(project_root=root)
    try:
        learner = IncrementalLearner(runtime, root, root.name)
        doc = root / "requirements.md"
        await learner._relearn(doc)
        before = await _items(default_db_path(), root.name)
        assert before and all(i["status"] == "active" for i in before)

        doc.write_text(_doc(_PAY_V2), encoding="utf-8")
        await learner._relearn(doc)
        after = await _items(default_db_path(), root.name)
        old_ids = {i["id"] for i in before}
        pay_old = [i for i in after if i["id"] in old_ids and "支付" in i["title"]]
        assert pay_old and all(i["status"] == "archived" for i in pay_old)
        login = [i for i in after if i["id"] in old_ids and "登录" in i["title"]]
        assert login and all(i["status"] == "active" for i in login)  # 未变章节保留
    finally:
        await runtime.close()


# ---------- 聚合类生成知识 churn 收敛 ----------


async def test_config_summary_churn_supersedes(tmp_path, monkeypatch):
    """配置摘要内容随依赖演进变化 → 旧版本 archived（此前 dedup 跳过新写但旧滞留）"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_text("# Demo\n\n## 使用\ndemo run\n", encoding="utf-8")
    pyproject = root / "pyproject.toml"
    pyproject.write_text('[project]\ndependencies = ["pydantic>=2"]\n', encoding="utf-8")

    assert await run_bootstrap(_args(root, scope="config")) == 0
    before = await _items(default_db_path(), root.name)
    cfg_before = [i for i in before if "signal:config" in (i["tags"] or "")]
    assert len(cfg_before) == 1

    pyproject.write_text(
        '[project]\ndependencies = ["pydantic>=2", "fastapi>=0.115"]\n', encoding="utf-8"
    )
    assert await run_bootstrap(_args(root, scope="config")) == 0
    after = await _items(default_db_path(), root.name)
    cfg_after = [i for i in after if "signal:config" in (i["tags"] or "")]
    assert len(cfg_after) == 2
    old = next(i for i in cfg_after if i["id"] == cfg_before[0]["id"])
    new = next(i for i in cfg_after if i["id"] != cfg_before[0]["id"])
    assert old["status"] == "archived"
    assert new["status"] == "pending_review"
    assert "fastapi" in new["content"]


async def test_rule_seed_churn_supersedes(tmp_path, monkeypatch):
    """用户规则文件改写（规范推翻）→ 旧种子 archived、新种子入队"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = tmp_path / "proj"
    rules = root / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\ndependencies = ["pydantic>=2"]\n', encoding="utf-8")
    (rules / "a.mdc").write_text("- 禁止使用裸 SQL，一律参数化查询\n", encoding="utf-8")

    assert await run_bootstrap(_args(root, scope="config")) == 0
    before = await _items(default_db_path(), root.name)
    seeds_before = [i for i in before if i["content_type"] == "prohibition"]
    assert len(seeds_before) == 1

    (rules / "a.mdc").write_text("- 禁止直调 Mapper，必须经 Service 层\n", encoding="utf-8")
    assert await run_bootstrap(_args(root, scope="config")) == 0
    after = await _items(default_db_path(), root.name)
    seeds_after = [i for i in after if i["content_type"] == "prohibition"]
    assert len(seeds_after) == 2
    old = next(i for i in seeds_after if i["id"] == seeds_before[0]["id"])
    new = next(i for i in seeds_after if i["id"] != seeds_before[0]["id"])
    assert old["status"] == "archived"
    assert new["status"] == "pending_review"
