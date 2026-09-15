"""ISSUE-2：bootstrap 审批队列限量（review_queue_cap）+ rsi_knowledge_review 批量审批。

实仓验证发现单次 bootstrap 产出 2308 条 pending_review，人工逐条审批不可用。
修复：bootstrap 按优先级限量入队（超出置 archived，可批量审批恢复）；
rsi_knowledge_review 支持 ids 数组 / all_pending 批量操作。
"""

import argparse
import json
import uuid
from pathlib import Path

from rsi_boot.api.tools import knowledge_review_tool
from rsi_boot.cli.bootstrap_command import run_bootstrap
from rsi_boot.core.models import KnowledgeItem
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc, type_from_legacy
from rsi_boot.services.knowledge_service import KnowledgeService

from memory_helpers import memory_item_rows


def _make_doc_project(tmp_path: Path, doc_count: int = 8) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    for i in range(doc_count):
        (docs / f"guide{i}.md").write_text(
            f"# 指南 {i}\n\n" + f"这是第 {i} 篇足够长的文档内容，" * 20, encoding="utf-8"
        )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["pydantic>=2"]\n', encoding="utf-8"
    )
    (tmp_path / "rsi-boot.yaml").write_text(
        "bootstrap:\n  review_queue_cap: 5\n", encoding="utf-8"
    )
    return tmp_path


def _args(root: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(root), scope="", dry_run=False, consent=False,
        then_start=False, force=False, strict=False, allow_sensitive=False,
        max_file_size="1MB", max_commits=500, include=[],
        host_judge=False, local_judge=True,
    )
    return argparse.Namespace(**{**defaults, **overrides})


async def _status_counts(root: Path, project_id: str = "") -> dict[str, int]:
    del project_id
    counts: dict[str, int] = {}
    for row in memory_item_rows(root):
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


async def test_bootstrap_caps_review_queue(tmp_path, monkeypatch):
    """帽只看 bootstrap_run_id，不再把车道 A 文档 archived；配置摘要直通 active。"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 0

    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["knowledge_written"] == 0
    assert report["pack_count"] >= 1
    counts = await _status_counts(root)
    assert counts.get("archived", 0) == 0
    assert (root / ".rsi" / "state" / "reading-packs" / "index.yaml").is_file()


async def test_bootstrap_force_rerun_does_not_requeue_archived(tmp_path, monkeypatch):
    """archived 内容参与去重：force 重跑不再入队，零新增"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    root = _make_doc_project(tmp_path / "proj")
    assert await run_bootstrap(_args(root)) == 0
    before = await _status_counts(root)

    assert await run_bootstrap(_args(root, force=True)) == 0
    report = json.loads((root / ".rsi" / "bootstrap_report.json").read_text(encoding="utf-8"))
    assert report["knowledge_written"] == 0
    after = await _status_counts(root)
    assert after == before  # 总量不变：archived 未被重新入队


# ---------- KnowledgeService：archived 可审批 + 批量审批 ----------


def _service(store: MemoryStore, tmp_path) -> KnowledgeService:
    return KnowledgeService(store=store, project_root=tmp_path)


async def _add(
    knowledge: KnowledgeService,
    status: str,
    content_type: str = "documentation",
    title: str = "条目",
    tags: list | None = None,
    store: MemoryStore | None = None,
) -> str:
    if status == "archived":
        mem = store or knowledge._store
        item_id = uuid.uuid4().hex
        typ, extra_update = type_from_legacy(content_type)
        dest = mem.rsi_dir / "memory" / "archive" / memory_filename(title, item_id)
        mem.write(
            MemoryDoc(
                id=item_id, type=typ, title=title[:120],
                content=f"{title} 的足够长内容 " * 5,
                tags=list(tags or []),
                extra=dict(extra_update),
            ),
            dest=dest,
        )
        return item_id
    return await knowledge.add(KnowledgeItem(
        project_id="p1", title=title, content=f"{title} 的足够长内容 " * 5,
        status=status, content_type=content_type, domain="bootstrap", tags=tags or [],
    ))


def _status_of(store: MemoryStore, item_id: str) -> str:
    return store.read(item_id).status


async def test_review_approves_archived_item(store, tmp_path):
    """archived（限量溢出）条目可被单个审批恢复为 active"""
    knowledge = _service(store, tmp_path)
    item_id = await _add(knowledge, "archived", store=store)
    assert await knowledge.review(item_id, "p1", approve=True) == "active"
    assert _status_of(store, item_id) == "active"


async def test_review_batch_approves_all_pending(store, tmp_path):
    knowledge = _service(store, tmp_path)
    ids = [await _add(knowledge, "pending_review", title=f"草稿{i}") for i in range(3)]
    active_id = await _add(knowledge, "active", title="已激活")

    result = await knowledge.review_batch("p1", approve=True)
    assert result["processed"] == 3
    assert result["new_status"] == "active"
    for item_id in ids:
        assert _status_of(store, item_id) == "active"
    assert _status_of(store, active_id) == "active"  # 原本 active 不受影响


async def test_review_batch_content_type_filter(store, tmp_path):
    knowledge = _service(store, tmp_path)
    faq_id = await _add(knowledge, "pending_review", content_type="documentation", title="问答")
    doc_id = await _add(knowledge, "pending_review", content_type="convention", title="文档")

    result = await knowledge.review_batch("p1", approve=True, content_type="documentation")
    assert result["processed"] == 1
    assert _status_of(store, faq_id) == "active"
    assert _status_of(store, doc_id) == "pending_review"  # 未命中过滤条件


async def test_review_batch_reject(store, tmp_path):
    knowledge = _service(store, tmp_path)
    ids = [await _add(knowledge, "pending_review", title=f"草稿{i}") for i in range(2)]
    result = await knowledge.review_batch("p1", approve=False)
    assert result["processed"] == 2
    assert result["new_status"] == "archived"
    for item_id in ids:
        assert _status_of(store, item_id) == "archived"


async def test_review_batch_include_archived(store, tmp_path):
    knowledge = _service(store, tmp_path)
    archived_id = await _add(
        knowledge, "archived", title="溢出",
        tags=["bootstrap_run_id:run-overflow"], store=store,
    )
    result = await knowledge.review_batch("p1", approve=True)
    assert result["processed"] == 0  # 默认不含 archived
    result = await knowledge.review_batch("p1", approve=True, include_archived=True)
    assert result["processed"] == 1
    assert _status_of(store, archived_id) == "active"


# ---------- rsi_knowledge_review 工具：批量参数 ----------


async def test_tool_all_pending(store, tmp_path):
    knowledge = _service(store, tmp_path)
    for i in range(3):
        await _add(knowledge, "pending_review", title=f"草稿{i}")
    result = await knowledge_review_tool.handle(
        knowledge, {"all_pending": True, "action": "approve", "project_id": "p1"}
    )
    assert result["status"] == "success"
    assert result["processed"] == 3


async def test_tool_ids_batch(store, tmp_path):
    knowledge = _service(store, tmp_path)
    ids = [await _add(knowledge, "pending_review", title=f"草稿{i}") for i in range(2)]
    other = await _add(knowledge, "pending_review", title="不在列表")
    result = await knowledge_review_tool.handle(
        knowledge, {"ids": ids, "action": "reject", "project_id": "p1"}
    )
    assert result["status"] == "success"
    assert result["processed"] == 2
    assert _status_of(store, other) == "pending_review"


async def test_tool_single_id_still_works(store, tmp_path):
    knowledge = _service(store, tmp_path)
    item_id = await _add(knowledge, "pending_review")
    result = await knowledge_review_tool.handle(
        knowledge, {"id": item_id, "action": "approve", "project_id": "p1"}
    )
    assert result["status"] == "success"
    assert result["new_status"] == "active"


async def test_tool_missing_target_is_error(store, tmp_path):
    knowledge = _service(store, tmp_path)
    result = await knowledge_review_tool.handle(
        knowledge, {"action": "approve", "project_id": "p1"}
    )
    assert result["status"] == "error"


def test_knowledge_review_schema_exposes_skip_and_bootstrap_run_id():
    props = knowledge_review_tool.INPUT_SCHEMA["properties"]
    assert "skip" in props["action"]["enum"]
    assert "bootstrap_run_id" in props


def test_all_pending_schema_does_not_claim_all_pending_review():
    desc = knowledge_review_tool.INPUT_SCHEMA["properties"]["all_pending"]["description"]
    assert "全部 pending_review" not in desc
    assert "auto-extract" in desc
    assert "bootstrap_run_id" in desc
