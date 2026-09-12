"""日常提取 vs 历史极性冲突：新稿 pending、历史保持 active、开 open incoherent。"""

from datetime import datetime, timezone
from pathlib import Path

from rsi_boot.core.models import KnowledgeItem
from rsi_boot.knowledge.embedding import EmbeddingService
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.learning.knowledge_extractor import KnowledgeExtractor
from rsi_boot.model.adapter import ModelAdapter
from rsi_boot.model.registry import ModelRegistry
from rsi_boot.scanner.conflict_gate import DraftItem, gate_drafts
from rsi_boot.services.knowledge_service import KnowledgeService

_NOW = datetime.now(timezone.utc).isoformat()
_PROHIBIT_BODY = "禁止使用 pydantic 作为入参。 " * 4
_PERMIT_BODY = "允许使用 pydantic 校验请求体。 " * 4


def _config():
    return {
        "model": {"default": "mock", "timeout_s": 5, "max_retries": 0},
        "retrieval": {"top_n": 5, "token_budget": 2000},
        "embedding": {"provider": "mock"},
    }


def _extractor(db):
    config = _config()
    embedding = EmbeddingService(config)
    adapter = ModelAdapter(ModelRegistry(config), config)
    extractor = KnowledgeExtractor(db, embedding, adapter, config)
    retriever = KnowledgeRetriever(db, config, embedding=embedding)
    knowledge = KnowledgeService(db, retriever, embedding=embedding)
    return extractor, knowledge


async def _insert_rejected(db, answer: str, project_id: str = "p1") -> None:
    conn = await db.connect()
    await conn.execute(
        "INSERT INTO extraction_candidates (id, project_id, source_log_id, candidate_type,"
        " question, answer, status, created_at)"
        " VALUES ('c-incoherent', ?, 'log1', 'rejected', 'API 校验', ?, 'pending', ?)",
        (project_id, answer, _NOW),
    )
    await conn.commit()


def test_gate_peers_incoherent_holds_draft_only():
    drafts = [
        DraftItem(
            "禁止：pydantic",
            _PROHIBIT_BODY,
            "prohibition",
            "auto-extract",
            ["signal:rules"],
            "rules",
        ),
    ]
    peers = [
        DraftItem(
            "API 用 pydantic",
            _PERMIT_BODY,
            "convention",
            "docs/api.md",
            ["signal:docs"],
            "docs",
        ),
    ]
    result = gate_drafts(drafts, [], Path("."), peers=peers)
    assert "auto-extract" in result.hold_sources
    assert "docs/api.md" not in result.hold_sources
    conflict = next(c for c in result.conflicts if c.conflict_type == "incoherent")
    assert conflict.left_source == "auto-extract"
    assert conflict.right_source == "docs/api.md"
    assert conflict.hold_sources == ["auto-extract"]


def test_gate_peers_skip_doc_code():
    from rsi_boot.scanner.code_scanner import ModuleSkeleton

    sk = ModuleSkeleton(rel_path="user.py", language="python", symbols=["User"])
    drafts = [
        DraftItem("Unrelated", "no identifiers here", "convention", "auto-extract", [], "rules"),
    ]
    peers = [
        DraftItem(
            "User",
            "See class MissingThing in the service layer. " * 5,
            "documentation",
            "docs/user.md",
            ["signal:docs"],
            "docs",
        ),
    ]
    result = gate_drafts(drafts, [sk], Path("."), peers=peers)
    assert not any(c.conflict_type == "doc_code" for c in result.conflicts)
    assert "docs/user.md" not in result.hold_sources


async def test_daily_new_prohibition_vs_active_permission_opens_incoherent(db):
    extractor, knowledge = _extractor(db)
    old_id = await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="API 用 pydantic",
        content=_PERMIT_BODY,
        content_type="convention",
        source_url="docs/api.md",
        status="active",
    ))
    await _insert_rejected(db, f"禁止：pydantic\n{_PROHIBIT_BODY}")

    stats = await extractor.run_daily()
    assert stats["extracted"] == 1
    assert stats["merged"] == 0

    items = await knowledge.list("p1")
    by_id = {i["id"]: i for i in items}
    assert by_id[old_id]["status"] == "active"
    new_items = [i for i in items if i["id"] != old_id]
    assert len(new_items) == 1
    new_id = new_items[0]["id"]
    assert new_items[0]["status"] == "pending_review"

    conn = await db.connect()
    async with conn.execute(
        "SELECT source_url, status FROM knowledge_items WHERE id = ?", (new_id,)
    ) as cur:
        new_row = await cur.fetchone()
    assert new_row["source_url"] == "auto-extract"
    assert new_row["status"] == "pending_review"

    async with conn.execute(
        "SELECT item_id, user_rule_path, user_rule_excerpt, conflict_type, status"
        " FROM rule_conflicts WHERE project_id = ?",
        ("p1",),
    ) as cur:
        conflicts = await cur.fetchall()
    assert len(conflicts) == 1
    row = conflicts[0]
    assert row["conflict_type"] == "incoherent"
    assert row["status"] == "open"
    assert row["item_id"] == new_id
    assert row["user_rule_path"] == "docs/api.md"
    assert old_id in row["user_rule_excerpt"]


async def test_empty_source_url_peer_still_persists_incoherent(db):
    extractor, knowledge = _extractor(db)
    old_id = await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="API 用 pydantic",
        content=_PERMIT_BODY,
        content_type="convention",
        source_url=None,
        status="active",
    ))
    await _insert_rejected(db, f"禁止：pydantic\n{_PROHIBIT_BODY}")

    stats = await extractor.run_daily()
    assert stats["extracted"] == 1

    items = await knowledge.list("p1")
    new_items = [i for i in items if i["id"] != old_id]
    assert len(new_items) == 1
    new_id = new_items[0]["id"]

    conn = await db.connect()
    async with conn.execute(
        "SELECT item_id, user_rule_path, user_rule_excerpt, conflict_type, status"
        " FROM rule_conflicts WHERE project_id = ?",
        ("p1",),
    ) as cur:
        conflicts = await cur.fetchall()
    assert len(conflicts) == 1
    row = conflicts[0]
    assert row["conflict_type"] == "incoherent"
    assert row["status"] == "open"
    assert row["item_id"] == new_id
    assert row["user_rule_path"] == f"item:{old_id}"
    assert old_id in row["user_rule_excerpt"]


async def test_duplicate_peer_source_url_unique_historical_ids(db):
    extractor, knowledge = _extractor(db)
    old_a = await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="API 用 pydantic A",
        content=_PERMIT_BODY,
        content_type="convention",
        source_url="docs/shared.md",
        status="active",
    ))
    old_b = await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="API 用 pydantic B",
        content=_PERMIT_BODY.replace("校验", "解析"),
        content_type="convention",
        source_url="docs/shared.md",
        status="active",
    ))
    await _insert_rejected(db, f"禁止：pydantic\n{_PROHIBIT_BODY}")

    stats = await extractor.run_daily()
    assert stats["extracted"] == 1

    conn = await db.connect()
    async with conn.execute(
        "SELECT user_rule_path, user_rule_excerpt FROM rule_conflicts WHERE project_id = ?",
        ("p1",),
    ) as cur:
        conflicts = await cur.fetchall()
    assert len(conflicts) == 2
    paths = {r["user_rule_path"] for r in conflicts}
    assert paths == {f"docs/shared.md#{old_a}", f"docs/shared.md#{old_b}"}
    excerpts = [r["user_rule_excerpt"] for r in conflicts]
    assert sum(old_a in e for e in excerpts) == 1
    assert sum(old_b in e for e in excerpts) == 1


async def test_identical_title_merges_without_conflict(db):
    extractor, knowledge = _extractor(db)
    await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="禁止：pydantic",
        content=_PROHIBIT_BODY,
        content_type="prohibition",
        source_url="docs/api.md",
        status="active",
    ))
    await _insert_rejected(db, f"禁止：pydantic\n{_PROHIBIT_BODY}")

    stats = await extractor.run_daily()
    assert stats["merged"] == 1
    assert stats["extracted"] == 0
    assert len(await knowledge.list("p1")) == 1

    conn = await db.connect()
    async with conn.execute("SELECT COUNT(*) AS n FROM rule_conflicts") as cur:
        assert (await cur.fetchone())["n"] == 0
