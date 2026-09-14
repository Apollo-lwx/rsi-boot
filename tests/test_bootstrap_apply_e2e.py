"""规格 §11 端到端：直通车道、审批帽隔离、accept 不误伤日常稿、explain 追问不关卡。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rsi_boot.api.tools import conflicts_tool
from rsi_boot.bootstrap import build_runtime
from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.cli.knowledge_accept import accept_bootstrap_extracts
from rsi_boot.core.models import KnowledgeItem
from rsi_boot.scanner.conflict_gate import ConflictDraft

from memory_helpers import memory_conflict_rows, memory_item_rows, memory_rows_from_sql


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500, include=[],
        host_judge=False, local_judge=True,
    )
    return argparse.Namespace(**{**defaults, **overrides})


def _long(prefix: str, n: int = 40) -> str:
    return (prefix + "。") * n


def _clean_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "README.md").write_text(
        "# Demo\n\n## 使用\n" + _long("这是一段足够长的仓库原文"),
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        '[project]\ndependencies = ["pydantic>=2"]\n', encoding="utf-8"
    )
    return root


def _src(row: dict) -> str:
    return (row.get("source_url") or "").replace("\\", "/")


def _tags(row: dict) -> list[str]:
    raw = row.get("tags") or ""
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def _is_extract(row: dict) -> bool:
    return bool({"signal:conversation", "signal:rules"} & set(_tags(row)))


async def _rows(root: Path, sql: str, params: tuple = ()):
    return await memory_rows_from_sql(root, sql, params)


async def _preinsert_auto_extract(root: Path, n: int = 10) -> list[str]:
    rt = await build_runtime(project_root=root)
    try:
        ids: list[str] = []
        for i in range(n):
            added = await rt.knowledge.add(KnowledgeItem(
                project_id=rt.project_id,
                title=f"日常提取 {i}",
                content=_long(f"从对话抽出的经验 {i}"),
                status="pending_review",
                content_type="experience",
                domain="daily",
                tags=["signal:auto-extract"],
                source_url="auto-extract",
            ))
            ids.append(added["id"] if isinstance(added, dict) else added)
        return ids
    finally:
        await rt.close()


async def _snapshot_kb(rt) -> dict:
    root = rt.store.rsi_dir.parent
    items = [
        (r["id"], r["status"], r.get("source_url"))
        for r in memory_item_rows(root)
    ]
    conflicts = [
        (r.get("id"), r.get("status"), r.get("resolution_note"))
        for r in memory_conflict_rows(root)
    ]
    return {"items": items, "conflicts": conflicts}


async def test_clean_repo_bootstrap_active_pending_only_extracts(tmp_path):
    """干净仓：active>0；无 conversation/rules 时 pending=0，否则 pending 仅为抽取。"""
    root = _clean_repo(tmp_path / "proj")

    assert await run_bootstrap(_args(root)) == 0

    rows = await _rows(
        root,
        "SELECT status, tags, source_url, content_type FROM knowledge_items WHERE project_id = ?",
    )
    active = [r for r in rows if r["status"] == "active"]
    pending = [r for r in rows if r["status"] == "pending_review"]
    assert len(active) > 0
    assert all(_is_extract(r) for r in pending)
    assert pending == []


async def test_doc_missing_class_held_code_skeleton_active(tmp_path):
    """文档点名 class MissingThing → 该文档 pending；代码骨架仍直通 active。"""
    root = _clean_repo(tmp_path / "proj")
    docs = root / "docs"
    docs.mkdir()
    (docs / "user.md").write_text(
        "# User\n\nSee class MissingThing in the service layer. "
        + _long("文档对照用户服务层接口"),
        encoding="utf-8",
    )
    (root / "user.py").write_text(
        "class User:\n    def name(self):\n        return 'ok'\n",
        encoding="utf-8",
    )

    assert await run_bootstrap(_args(root)) == 0

    rows = await _rows(
        root,
        "SELECT status, source_url, tags FROM knowledge_items WHERE project_id = ?",
    )
    doc_rows = [r for r in rows if _src(r).startswith("docs/user.md")]
    code_rows = [r for r in rows if _src(r) == "signal:code"]
    assert doc_rows
    assert all(r["status"] == "pending_review" for r in doc_rows)
    assert code_rows
    assert all(r["status"] == "active" for r in code_rows)

    conflicts = await _rows(
        root,
        "SELECT conflict_type, item_id FROM rule_conflicts WHERE project_id = ?",
    )
    assert any(c["conflict_type"] == "doc_code" for c in conflicts)


async def test_review_queue_cap_does_not_archive_auto_extract(tmp_path):
    """review_queue_cap=2 且预置 10 条 auto-extract → bootstrap 后这 10 条仍 pending。"""
    root = _clean_repo(tmp_path / "proj")
    (root / "rsi-boot.yaml").write_text(
        "bootstrap:\n  review_queue_cap: 2\n", encoding="utf-8"
    )
    await _preinsert_auto_extract(root, 10)

    assert await run_bootstrap(_args(root)) == 0

    autos = await _rows(
        root,
        "SELECT status FROM knowledge_items "
        "WHERE project_id = ? AND source_url = 'auto-extract'",
    )
    assert len(autos) == 10
    assert all(r["status"] == "pending_review" for r in autos)


async def test_accept_activates_bootstrap_extracts_leaves_auto_extract(tmp_path):
    """accept 后本轮抽取变 active；预置 source_url=auto-extract 仍 pending。"""
    root = _clean_repo(tmp_path / "proj")
    rules = root / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "team.mdc").write_text(
        "- 禁止使用 eval 执行用户输入\n",
        encoding="utf-8",
    )
    await _preinsert_auto_extract(root, 3)

    assert await run_bootstrap(_args(root)) == 0

    extracts_before = await _rows(
        root,
        "SELECT id, status, tags, source_url FROM knowledge_items WHERE project_id = ?",
    )
    extract_ids = [
        r["id"] for r in extracts_before
        if r["status"] == "pending_review" and _is_extract(r)
    ]
    assert extract_ids

    rt = await build_runtime(project_root=root)
    try:
        result = await accept_bootstrap_extracts(
            rt, run_id=None, reject=False, conflicts=None,
        )
        assert result["processed"] == len(extract_ids)
    finally:
        await rt.close()

    after = await _rows(
        root,
        "SELECT id, status, tags, source_url FROM knowledge_items WHERE project_id = ?",
    )
    by_id = {r["id"]: r for r in after}
    for eid in extract_ids:
        assert by_id[eid]["status"] == "active"
    autos = [r for r in after if _src(r) == "auto-extract"]
    assert len(autos) == 3
    assert all(r["status"] == "pending_review" for r in autos)


async def test_meikan_dong_explain_must_not_skip_or_close_decision(tmp_path):
    """「没看懂 / 展开」走 rsi_conflicts explain：不改库、不 suppress、同一 decision.id 仍 open。

    若有人把「没看懂」做成 skip，第二次 recall 会丢掉这张卡，本用例必须失败。
    """
    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    try:
        pid = rt.project_id
        left_id = await rt.knowledge.add(KnowledgeItem(
            project_id=pid,
            title="禁止：pydantic",
            content="禁止使用 pydantic 做请求校验。" * 4,
            status="pending_review",
            content_type="prohibition",
            tags=["signal:conversation"],
            source_url="auto-extract",
        ))
        right_id = await rt.knowledge.add(KnowledgeItem(
            project_id=pid,
            title="API 用 pydantic",
            content="允许使用 pydantic 做请求校验。" * 4,
            status="active",
            content_type="convention",
            tags=["signal:docs"],
            source_url="docs/api.md",
        ))
        n = await rt.conflict_detector.persist_knowledge_conflicts(
            pid,
            [ConflictDraft(
                conflict_type="incoherent",
                left_source="auto-extract",
                right_source="docs/api.md",
                reason="极性相反：禁止 pydantic vs 允许 pydantic",
                hold_sources=["auto-extract"],
                recommended="keep_item",
                recommended_reason="新稿来自刚才这次对话的明确否定",
            )],
            {"auto-extract": left_id, "docs/api.md": right_id},
        )
        assert n == 1

        first = await rt.recall.recall("API 校验怎么写", pid)
        assert first["decisions"]
        decision_id = first["decisions"][0]["id"]
        assert first["decisions"][0]["kind"] == "daily_conflict"

        before = await _snapshot_kb(rt)
        explained = await conflicts_tool.handle(rt, {
            "action": "explain",
            "conflict_id": decision_id,
        })
        assert explained["status"] == "success"
        assert explained["data"]["sides"]
        after = await _snapshot_kb(rt)
        assert after == before

        second = await rt.recall.recall("没看懂，详细说说", pid)
        assert second["decisions"], "explain / 没看懂 不得 suppress 或关掉抉择卡"
        assert second["decisions"][0]["id"] == decision_id
        assert decision_id not in rt.decisions._closed
        expiry = rt.decisions._suppressed.get(decision_id)
        assert expiry is None, "没看懂 不得当成 skip 抑制这张卡"
    finally:
        await rt.close()
