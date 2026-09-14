"""日常提取 vs 历史极性冲突：新稿 pending、历史保持 active、开 open incoherent。"""

from pathlib import Path

from rsi_boot.core.models import KnowledgeItem
from rsi_boot.injector.conflict import ConflictDetector
from rsi_boot.learning.knowledge_extractor import KnowledgeExtractor
from rsi_boot.learning.pipeline import extract_from_feedback
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.scanner.conflict_gate import DraftItem, gate_drafts
from rsi_boot.services.knowledge_service import KnowledgeService

from memory_helpers import memory_conflict_rows, write_memory_conflict

_PROHIBIT_BODY = "禁止使用 pydantic 作为入参。 " * 4
_PERMIT_BODY = "允许使用 pydantic 作为入参。 " * 4
_PERMIT_BODY_B = _PERMIT_BODY.replace("。 ", "，解析请求体。 ", 1)


def _services(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    extractor = KnowledgeExtractor(store=store)
    knowledge = KnowledgeService(store=store, project_root=tmp_path)
    return store, extractor, knowledge


def _seed_official(store: MemoryStore, *, title: str, content: str, source: str | None = None) -> str:
    import uuid
    item_id = uuid.uuid4().hex
    extra = {"source_url": source} if source else {}
    store.write(
        MemoryDoc(
            id=item_id, type="convention", title=title, content=content,
            source=source, extra=extra,
        ),
        dest=official_dir(store.rsi_dir, "convention") / memory_filename(title, item_id),
    )
    return item_id


def _extract_rejected(store: MemoryStore, answer: str) -> str:
    paths = extract_from_feedback(
        store.rsi_dir, action="rejected", comment=answer, question="API 校验",
    )
    assert paths
    docs = [d for d in store.list_pending("prohibition") if d.source == "auto-extract"]
    assert docs
    return docs[-1].id


def _persist_incoherent(store: MemoryStore, tmp_path, new_id: str, peer_source: str) -> int:
    new_doc = store.read(new_id)
    drafts = [
        DraftItem(
            new_doc.title, new_doc.content, "prohibition", "auto-extract",
            ["signal:rules"], "rules",
        ),
    ]
    peer_doc = None
    if peer_source.startswith("item:"):
        peer_doc = store.read(peer_source.split(":", 1)[1])
        peer_url = peer_source
    else:
        peers = [d for d in store.list_official() if (d.extra.get("source_url") or d.source) == peer_source]
        peer_doc = peers[0] if peers else None
        peer_url = peer_source
    peers = []
    if peer_doc is not None:
        peers = [
            DraftItem(
                peer_doc.title, peer_doc.content, peer_doc.type, peer_url,
                ["signal:docs"], "docs",
            ),
        ]
    gated = gate_drafts(drafts, [], Path(tmp_path), peers=peers)
    det = ConflictDetector(store=store, project_root=tmp_path)
    return det, gated, new_doc


def test_gate_peers_incoherent_holds_draft_only():
    drafts = [
        DraftItem(
            "pydantic 入参",
            _PROHIBIT_BODY,
            "prohibition",
            "auto-extract",
            ["signal:rules"],
            "rules",
        ),
    ]
    peers = [
        DraftItem(
            "pydantic 入参",
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


def test_gate_peers_different_object_not_paired():
    """日常提取 vs 历史：对象不同（明文 vs 加密密码）不开 incoherent。"""
    drafts = [
        DraftItem("密码存储", "禁止写入明文密码到数据库。 " * 6,
                  "prohibition", "auto-extract", ["signal:rules"], "rules"),
    ]
    peers = [
        DraftItem("密码存储", "允许写入加密密码到数据库。 " * 6,
                  "convention", "docs/crypto.md", ["signal:docs"], "docs"),
    ]
    result = gate_drafts(drafts, [], Path("."), peers=peers)
    assert not any(c.conflict_type == "incoherent" for c in result.conflicts)
    assert "auto-extract" not in result.hold_sources


async def test_daily_new_prohibition_vs_active_permission_opens_incoherent(tmp_path):
    store, extractor, knowledge = _services(tmp_path)
    old_id = await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="pydantic 入参",
        content=_PERMIT_BODY,
        content_type="convention",
        source_url="docs/api.md",
        status="active",
    ))
    new_id = _extract_rejected(store, f"pydantic 入参\n{_PROHIBIT_BODY}")

    items = await knowledge.list("p1")
    by_id = {i["id"]: i for i in items}
    assert by_id[old_id]["status"] == "active"
    new_items = [i for i in items if i["id"] != old_id]
    assert len(new_items) == 1
    assert new_items[0]["id"] == new_id
    assert new_items[0]["status"] == "pending_review"

    new_row = store.read(new_id)
    assert (new_row.source or new_row.extra.get("source_url")) == "auto-extract"
    assert new_row.status == "pending_review"

    det, gated, _ = _persist_incoherent(store, tmp_path, new_id, "docs/api.md")
    n = await det.persist_knowledge_conflicts(
        "p1", gated.conflicts, {"auto-extract": new_id, "docs/api.md": old_id},
    )
    assert n == 1
    conflicts = memory_conflict_rows(tmp_path)
    assert len(conflicts) == 1
    row = conflicts[0]
    assert (row.get("conflict_type") or row.get("type")) == "incoherent"
    assert row["status"] == "open"
    assert row["item_id"] == new_id
    assert row["user_rule_path"] == "docs/api.md"


async def test_empty_source_url_peer_still_persists_incoherent(tmp_path):
    store, extractor, knowledge = _services(tmp_path)
    old_id = _seed_official(
        store, title="pydantic 入参", content=_PERMIT_BODY, source=None,
    )
    new_id = _extract_rejected(store, f"pydantic 入参\n{_PROHIBIT_BODY}")

    items = await knowledge.list("p1")
    new_items = [i for i in items if i["id"] != old_id]
    assert len(new_items) == 1
    assert new_items[0]["id"] == new_id

    peer_key = f"item:{old_id}"
    det, gated, _ = _persist_incoherent(store, tmp_path, new_id, peer_key)
    n = await det.persist_knowledge_conflicts(
        "p1", gated.conflicts, {"auto-extract": new_id, peer_key: old_id},
    )
    assert n == 1
    conflicts = memory_conflict_rows(tmp_path)
    assert len(conflicts) == 1
    row = conflicts[0]
    assert (row.get("conflict_type") or row.get("type")) == "incoherent"
    assert row["status"] == "open"
    assert row["item_id"] == new_id
    assert row["user_rule_path"] == peer_key
    assert old_id in (row.get("user_rule_path") or "")


async def test_duplicate_peer_source_url_unique_historical_ids(tmp_path):
    store, extractor, knowledge = _services(tmp_path)
    old_a = await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="pydantic 入参",
        content=_PERMIT_BODY,
        content_type="convention",
        source_url="docs/shared.md",
        status="active",
    ))
    old_b = await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="pydantic 入参",
        content=_PERMIT_BODY_B,
        content_type="convention",
        source_url="docs/shared.md",
        status="active",
    ))
    new_id = _extract_rejected(store, f"pydantic 入参\n{_PROHIBIT_BODY}")

    write_memory_conflict(store, item_id=new_id, peer_source=f"docs/shared.md#{old_a}")
    write_memory_conflict(store, item_id=new_id, peer_source=f"docs/shared.md#{old_b}")
    conflicts = memory_conflict_rows(tmp_path)
    assert len(conflicts) == 2
    paths = {r["user_rule_path"] for r in conflicts}
    assert paths == {f"docs/shared.md#{old_a}", f"docs/shared.md#{old_b}"}
    excerpts = [r.get("user_rule_excerpt") or r.get("excerpt") or "" for r in conflicts]
    assert len(excerpts) == 2


async def test_keep_item_item_key_archives_only_that_history(tmp_path):
    store, extractor, knowledge = _services(tmp_path)
    old_id = _seed_official(
        store, title="pydantic 入参", content=_PERMIT_BODY, source=None,
    )
    unrelated_a = await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="无关日常 A",
        content="从对话抽出的无关经验甲。" * 8,
        content_type="experience",
        source_url="auto-extract",
        status="pending_review",
    ))
    unrelated_b = await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="无关日常 B",
        content="从对话抽出的无关经验乙。" * 8,
        content_type="experience",
        source_url="auto-extract",
        status="pending_review",
    ))
    new_id = _extract_rejected(store, f"pydantic 入参\n{_PROHIBIT_BODY}")

    cid = write_memory_conflict(store, item_id=new_id, peer_source=f"item:{old_id}")
    result = await ConflictDetector(store=store, project_root=tmp_path).resolve(cid, "keep_item")
    assert result is not None

    rows = {d.id: d.status for d in store.list_all()}
    assert rows[old_id] == "archived"
    assert rows[new_id] == "active"
    assert rows[unrelated_a] == "pending_review"
    assert rows[unrelated_b] == "pending_review"


async def test_keep_item_hashed_url_archives_only_hashed_peer(tmp_path):
    store, extractor, knowledge = _services(tmp_path)
    old_a = await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="pydantic 入参",
        content=_PERMIT_BODY,
        content_type="convention",
        source_url="docs/shared.md",
        status="active",
    ))
    old_b = await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="pydantic 入参",
        content=_PERMIT_BODY_B,
        content_type="convention",
        source_url="docs/shared.md",
        status="active",
    ))
    new_id = _extract_rejected(store, f"pydantic 入参\n{_PROHIBIT_BODY}")

    cid_a = write_memory_conflict(store, item_id=new_id, peer_source=f"item:{old_a}")
    write_memory_conflict(store, item_id=new_id, peer_source=f"item:{old_b}")

    result = await ConflictDetector(store=store, project_root=tmp_path).resolve(cid_a, "keep_item")
    assert result is not None

    rows = {d.id: d.status for d in store.list_all()}
    assert rows[old_a] == "archived"
    assert rows[old_b] == "active"
    assert rows[new_id] == "active"


async def test_identical_title_merges_without_conflict(tmp_path):
    store, extractor, knowledge = _services(tmp_path)
    await knowledge.add(KnowledgeItem(
        project_id="p1",
        title="禁止：pydantic",
        content=_PROHIBIT_BODY,
        content_type="prohibition",
        source_url="docs/api.md",
        status="active",
    ))
    extractor.extract_from_feedback(
        action="rejected",
        comment=f"禁止：pydantic\n{_PROHIBIT_BODY}",
        question="API 校验",
    )

    drafts = [
        DraftItem("禁止：pydantic", _PROHIBIT_BODY, "prohibition", "auto-extract", ["signal:rules"], "rules"),
    ]
    peers = [
        DraftItem("禁止：pydantic", _PROHIBIT_BODY, "prohibition", "docs/api.md", ["signal:docs"], "docs"),
    ]
    gated = gate_drafts(drafts, [], Path(tmp_path), peers=peers)
    assert not any(c.conflict_type == "incoherent" for c in gated.conflicts)
    assert memory_conflict_rows(tmp_path) == []
