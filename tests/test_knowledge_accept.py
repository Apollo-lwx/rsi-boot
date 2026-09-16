"""Task 7：review_batch 过滤 + rsi knowledge accept 只放行本轮 bootstrap 抽取。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from rsi_boot.api.tools import knowledge_review_tool
from rsi_boot.bootstrap import build_runtime
from rsi_boot.core.models import KnowledgeItem
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc, type_from_legacy
from rsi_boot.services.knowledge_service import KnowledgeService

from memory_helpers import write_memory_conflict


def _service(store: MemoryStore, tmp_path) -> KnowledgeService:
    return KnowledgeService(store=store, project_root=tmp_path)


async def _add(
    knowledge: KnowledgeService,
    *,
    title: str,
    status: str = "pending_review",
    content_type: str = "convention",
    tags: list[str] | None = None,
    source_url: str | None = None,
    project_id: str = "p1",
    store: MemoryStore | None = None,
) -> str:
    if status == "archived":
        mem = store or knowledge._store
        item_id = uuid.uuid4().hex
        typ, extra_update = type_from_legacy(content_type)
        extra = dict(extra_update)
        if source_url:
            extra["source_url"] = source_url
        dest = mem.rsi_dir / "memory" / "archive" / memory_filename(title, item_id)
        mem.write(
            MemoryDoc(
                id=item_id, type=typ, title=title[:120],
                content=f"{title} 的足够长内容 " * 5,
                tags=list(tags or []),
                source=source_url or None,
                extra=extra,
            ),
            dest=dest,
        )
        return item_id
    added = await knowledge.add(KnowledgeItem(
        project_id=project_id, title=title, content=f"{title} 的足够长内容 " * 5,
        status=status, content_type=content_type, tags=tags or [], source_url=source_url,
    ))
    return added["id"] if isinstance(added, dict) else added


def _status_of(holder, item_id: str) -> str:
    store = getattr(holder, "store", holder)
    return store.read(item_id).status


async def test_review_batch_filters_bootstrap_run_id(store, tmp_path):
    knowledge = _service(store, tmp_path)
    keep = await _add(
        knowledge, title="本轮", tags=["signal:conversation", "bootstrap_run_id:run-a"],
    )
    other = await _add(
        knowledge, title="上轮", tags=["signal:conversation", "bootstrap_run_id:run-b"],
    )
    result = await knowledge.review_batch("p1", approve=True, bootstrap_run_id="run-a")
    assert result["processed"] == 1
    assert _status_of(store, keep) == "active"
    assert _status_of(store, other) == "pending_review"


async def test_review_batch_exclude_bootstrap(store, tmp_path):
    knowledge = _service(store, tmp_path)
    daily = await _add(knowledge, title="日常", source_url="auto-extract")
    boot = await _add(
        knowledge, title="抽取", tags=["signal:rules", "bootstrap_run_id:run-a"],
    )
    result = await knowledge.review_batch("p1", approve=True, exclude_bootstrap=True)
    assert result["processed"] == 1
    assert _status_of(store, daily) == "active"
    assert _status_of(store, boot) == "pending_review"


async def test_review_batch_source_url_filter(store, tmp_path):
    knowledge = _service(store, tmp_path)
    hit = await _add(knowledge, title="命中", source_url="auto-extract")
    miss = await _add(knowledge, title="其它", source_url="docs/a.md")
    result = await knowledge.review_batch("p1", approve=True, source_url="auto-extract")
    assert result["processed"] == 1
    assert _status_of(store, hit) == "active"
    assert _status_of(store, miss) == "pending_review"


async def test_include_archived_skips_untagged_overflow(store, tmp_path):
    knowledge = _service(store, tmp_path)
    overflow = await _add(knowledge, title="旧溢出", status="archived", tags=[], store=store)
    tagged = await _add(
        knowledge, title="本轮溢出", status="archived",
        tags=["signal:docs", "bootstrap_run_id:run-a"], store=store,
    )
    result = await knowledge.review_batch(
        "p1", approve=True, include_archived=True, exclude_bootstrap=True,
    )
    assert _status_of(store, overflow) == "archived"
    assert result["processed"] == 0
    result = await knowledge.review_batch(
        "p1", approve=True, include_archived=True, bootstrap_run_id="run-a",
    )
    assert result["processed"] == 1
    assert _status_of(store, tagged) == "active"
    assert _status_of(store, overflow) == "archived"


async def test_review_batch_run_id_skips_auto_extract(store, tmp_path):
    """带 bootstrap_run_id 的批量审批不得放行 source_url=auto-extract。"""
    knowledge = _service(store, tmp_path)
    leaked = await _add(
        knowledge, title="误标日常",
        source_url="auto-extract",
        tags=["signal:conversation", "bootstrap_run_id:run-a"],
    )
    extract = await _add(
        knowledge, title="本轮抽取",
        source_url="cursor/chat.md",
        tags=["signal:conversation", "bootstrap_run_id:run-a"],
    )
    result = await knowledge.review_batch("p1", approve=True, bootstrap_run_id="run-a")
    assert result["processed"] == 1
    assert _status_of(store, extract) == "active"
    assert _status_of(store, leaked) == "pending_review"


async def test_all_pending_does_not_approve_bootstrap_run_items(store, tmp_path):
    knowledge = _service(store, tmp_path)
    daily = await _add(knowledge, title="日常草稿", source_url="auto-extract")
    boot = await _add(
        knowledge, title="bootstrap 抽取",
        tags=["signal:conversation", "bootstrap_run_id:run-z"],
        source_url="cursor/chat.md",
    )
    result = await knowledge_review_tool.handle(
        knowledge, {"all_pending": True, "action": "approve", "project_id": "p1"}
    )
    assert result["status"] == "success"
    assert result["processed"] == 1
    assert _status_of(store, daily) == "active"
    assert _status_of(store, boot) == "pending_review"


def _write_run(root: Path, run_id: str) -> None:
    rsi = root / ".rsi"
    rsi.mkdir(parents=True, exist_ok=True)
    (rsi / "bootstrap_run.json").write_text(
        json.dumps({"latest": run_id}, ensure_ascii=False), encoding="utf-8",
    )


async def test_accept_releases_conversation_faq_documentation(tmp_path):
    from rsi_boot.cli.knowledge_accept import accept_bootstrap_extracts

    root = tmp_path / "proj"
    root.mkdir()
    run_id = "run-faq"
    _write_run(root, run_id)
    rt = await build_runtime(project_root=root)
    try:
        faq_id = await _add(
            rt.knowledge, title="对话常见问题", project_id=rt.project_id,
            content_type="faq", source_url="cursor/chat.md",
            tags=["signal:conversation", f"bootstrap_run_id:{run_id}"],
        )
        result = await accept_bootstrap_extracts(
            rt, run_id=None, reject=False, conflicts=None,
        )
        assert result["processed"] == 1
        assert _status_of(rt, faq_id) == "active"
    finally:
        await rt.close()


async def test_review_bootstrap_run_id_approves_distilled(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    run_id = "run-distill-review"
    _write_run(root, run_id)
    rt = await build_runtime(project_root=root)
    try:
        distilled_id = await _add(
            rt.knowledge, title="模块依赖禁止项", project_id=rt.project_id,
            content_type="prohibition", source_url=".cursor/skills/gateway-jdbc/SKILL.md",
            tags=["signal:distilled", f"bootstrap_run_id:{run_id}", "pack_id:skills-0000"],
        )
        result = await knowledge_review_tool.handle(rt, {
            "action": "approve",
            "bootstrap_run_id": run_id,
        })
        assert result["status"] == "success"
        assert result["processed"] == 1
        assert _status_of(rt, distilled_id) == "active"
    finally:
        await rt.close()


async def test_review_bootstrap_run_id_includes_faq(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    run_id = "run-faq-review"
    _write_run(root, run_id)
    rt = await build_runtime(project_root=root)
    try:
        faq_id = await _add(
            rt.knowledge, title="决策记录", project_id=rt.project_id,
            content_type="faq", source_url="cursor/decisions.md",
            tags=["signal:conversation", f"bootstrap_run_id:{run_id}"],
        )
        result = await knowledge_review_tool.handle(rt, {
            "action": "approve",
            "bootstrap_run_id": run_id,
        })
        assert result["status"] == "success"
        assert result["processed"] == 1
        assert _status_of(rt, faq_id) == "active"
    finally:
        await rt.close()


async def test_accept_only_releases_this_run_extracts_not_auto_extract(tmp_path):
    from rsi_boot.cli.knowledge_accept import accept_bootstrap_extracts

    root = tmp_path / "proj"
    root.mkdir()
    run_id = "run-this"
    _write_run(root, run_id)
    rt = await build_runtime(project_root=root)
    try:
        pid = rt.project_id
        auto_id = await _add(
            rt.knowledge, title="日常提取", project_id=pid,
            source_url="auto-extract", tags=["signal:conversation"],
        )
        extract_id = await _add(
            rt.knowledge, title="本轮抽取", project_id=pid,
            source_url="cursor/chat.md",
            tags=["signal:conversation", f"bootstrap_run_id:{run_id}"],
        )
        docs_id = await _add(
            rt.knowledge, title="冲突文档", project_id=pid,
            source_url="docs/foo.md",
            tags=["signal:docs", f"bootstrap_run_id:{run_id}"],
        )
        other_run = await _add(
            rt.knowledge, title="上轮抽取", project_id=pid,
            source_url="cursor/old.md",
            tags=["signal:rules", "bootstrap_run_id:run-old"],
        )
        result = await accept_bootstrap_extracts(
            rt, run_id=None, reject=False, conflicts=None,
        )
        assert result["processed"] == 1
        assert _status_of(rt, extract_id) == "active"
        assert _status_of(rt, auto_id) == "pending_review"
        assert _status_of(rt, docs_id) == "pending_review"
        assert _status_of(rt, other_run) == "pending_review"
    finally:
        await rt.close()


async def test_accept_no_extracts_prints_and_exits_zero(tmp_path, capsys):
    from rsi_boot.cli.knowledge_accept import run_knowledge_accept

    root = tmp_path / "proj"
    root.mkdir()
    rt = await build_runtime(project_root=root)
    try:
        code = await run_knowledge_accept(rt, run_id=None, reject=False, conflicts=None)
        assert code == 0
        assert "本轮无抽取项" in capsys.readouterr().out
    finally:
        await rt.close()


async def test_accept_conflicts_tend_uses_recommended(tmp_path):
    from rsi_boot.cli.knowledge_accept import accept_bootstrap_extracts

    root = tmp_path / "proj"
    root.mkdir()
    run_id = "run-cf"
    _write_run(root, run_id)
    rt = await build_runtime(project_root=root)
    try:
        pid = rt.project_id
        left = "docs/new.md"
        right = "docs/old.md"
        left_id = await _add(
            rt.knowledge, title="禁止 pydantic", project_id=pid,
            source_url=left, content_type="prohibition",
            tags=["signal:docs", f"bootstrap_run_id:{run_id}"],
        )
        right_id = await _add(
            rt.knowledge, title="允许 pydantic", project_id=pid,
            source_url=right,
            tags=["signal:docs", f"bootstrap_run_id:{run_id}"],
        )
        write_memory_conflict(
            rt.store, item_id=left_id, peer_source=right,
            excerpt="极性相反",
        )
        result = await accept_bootstrap_extracts(
            rt, run_id=run_id, reject=False, conflicts="tend",
        )
        assert result["conflicts_resolved"] == 1
        assert _status_of(rt, left_id) == "active"
        assert _status_of(rt, right_id) == "archived"
        from memory_helpers import memory_conflict_rows

        rows = memory_conflict_rows(root)
        assert rows and all(r.get("status") != "open" for r in rows)
    finally:
        await rt.close()


def test_cli_accept_flags():
    from rsi_boot.__main__ import build_parser

    args = build_parser().parse_args(
        ["knowledge", "accept", "--reject", "--conflicts", "tend", "--run", "abc"]
    )
    assert args.knowledge_action == "accept"
    assert args.reject is True
    assert args.conflicts == "tend"
    assert args.run == "abc"
