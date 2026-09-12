"""Task 7：review_batch 过滤 + rsi knowledge accept 只放行本轮 bootstrap 抽取。"""

from __future__ import annotations

import json
from pathlib import Path

from rsi_boot.api.tools import knowledge_review_tool
from rsi_boot.bootstrap import build_runtime
from rsi_boot.core.models import KnowledgeItem
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.scanner.conflict_gate import ConflictDraft
from rsi_boot.services.knowledge_service import KnowledgeService


def _service(db, base_config) -> KnowledgeService:
    return KnowledgeService(db, KnowledgeRetriever(db, base_config))


async def _add(
    knowledge: KnowledgeService,
    *,
    title: str,
    status: str = "pending_review",
    content_type: str = "convention",
    tags: list[str] | None = None,
    source_url: str | None = None,
    project_id: str = "p1",
) -> str:
    return await knowledge.add(KnowledgeItem(
        project_id=project_id, title=title, content=f"{title} 的足够长内容 " * 5,
        status=status, content_type=content_type, tags=tags or [], source_url=source_url,
    ))


async def _status_of(db, item_id: str) -> str:
    conn = await db.connect()
    async with conn.execute(
        "SELECT status FROM knowledge_items WHERE id = ?", (item_id,)
    ) as cur:
        row = await cur.fetchone()
    return row["status"]


async def test_review_batch_filters_bootstrap_run_id(db, base_config):
    knowledge = _service(db, base_config)
    keep = await _add(
        knowledge, title="本轮", tags=["signal:conversation", "bootstrap_run_id:run-a"],
    )
    other = await _add(
        knowledge, title="上轮", tags=["signal:conversation", "bootstrap_run_id:run-b"],
    )
    result = await knowledge.review_batch("p1", approve=True, bootstrap_run_id="run-a")
    assert result["processed"] == 1
    assert await _status_of(db, keep) == "active"
    assert await _status_of(db, other) == "pending_review"


async def test_review_batch_exclude_bootstrap(db, base_config):
    knowledge = _service(db, base_config)
    daily = await _add(knowledge, title="日常", source_url="auto-extract")
    boot = await _add(
        knowledge, title="抽取", tags=["signal:rules", "bootstrap_run_id:run-a"],
    )
    result = await knowledge.review_batch("p1", approve=True, exclude_bootstrap=True)
    assert result["processed"] == 1
    assert await _status_of(db, daily) == "active"
    assert await _status_of(db, boot) == "pending_review"


async def test_review_batch_source_url_filter(db, base_config):
    knowledge = _service(db, base_config)
    hit = await _add(knowledge, title="命中", source_url="auto-extract")
    miss = await _add(knowledge, title="其它", source_url="docs/a.md")
    result = await knowledge.review_batch("p1", approve=True, source_url="auto-extract")
    assert result["processed"] == 1
    assert await _status_of(db, hit) == "active"
    assert await _status_of(db, miss) == "pending_review"


async def test_include_archived_skips_untagged_overflow(db, base_config):
    knowledge = _service(db, base_config)
    overflow = await _add(knowledge, title="旧溢出", status="archived", tags=[])
    tagged = await _add(
        knowledge, title="本轮溢出", status="archived",
        tags=["signal:docs", "bootstrap_run_id:run-a"],
    )
    result = await knowledge.review_batch(
        "p1", approve=True, include_archived=True, exclude_bootstrap=True,
    )
    assert await _status_of(db, overflow) == "archived"
    assert result["processed"] == 0
    result = await knowledge.review_batch(
        "p1", approve=True, include_archived=True, bootstrap_run_id="run-a",
    )
    assert result["processed"] == 1
    assert await _status_of(db, tagged) == "active"
    assert await _status_of(db, overflow) == "archived"


async def test_review_batch_run_id_skips_auto_extract(db, base_config):
    """带 bootstrap_run_id 的批量审批不得放行 source_url=auto-extract。"""
    knowledge = _service(db, base_config)
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
    assert await _status_of(db, extract) == "active"
    assert await _status_of(db, leaked) == "pending_review"


async def test_all_pending_does_not_approve_bootstrap_run_items(db, base_config):
    knowledge = _service(db, base_config)
    daily = await _add(knowledge, title="日常草稿", source_url="auto-extract")
    boot = await _add(
        knowledge, title="bootstrap 抽取",
        tags=["signal:conversation", "bootstrap_run_id:run-z"],
        source_url="cursor/chat.md",
    )
    result = await knowledge_review_tool.handle(
        knowledge, {"all_pending": True, "action": "approve", "project_id": "p1"}
    )
    assert result["status"] == "ok"
    assert result["processed"] == 1
    assert await _status_of(db, daily) == "active"
    assert await _status_of(db, boot) == "pending_review"


def _write_run(root: Path, run_id: str) -> None:
    rsi = root / ".rsi"
    rsi.mkdir(parents=True, exist_ok=True)
    (rsi / "bootstrap_run.json").write_text(
        json.dumps({"latest": run_id}, ensure_ascii=False), encoding="utf-8",
    )


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
        assert await _status_of(rt.db, extract_id) == "active"
        assert await _status_of(rt.db, auto_id) == "pending_review"
        assert await _status_of(rt.db, docs_id) == "pending_review"
        assert await _status_of(rt.db, other_run) == "pending_review"
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
        n = await rt.conflict_detector.persist_knowledge_conflicts(
            pid,
            [ConflictDraft(
                conflict_type="incoherent",
                left_source=left, right_source=right,
                reason="极性相反", hold_sources=[left, right],
                recommended="keep_item", recommended_reason="新稿",
            )],
            {left: left_id, right: right_id},
        )
        assert n == 1
        result = await accept_bootstrap_extracts(
            rt, run_id=run_id, reject=False, conflicts="tend",
        )
        assert result["conflicts_resolved"] == 1
        assert await _status_of(rt.db, left_id) == "active"
        assert await _status_of(rt.db, right_id) == "archived"
        conn = await rt.db.connect()
        async with conn.execute(
            "SELECT status FROM rule_conflicts WHERE project_id = ?", (pid,),
        ) as cur:
            row = await cur.fetchone()
        assert row["status"] != "open"
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
