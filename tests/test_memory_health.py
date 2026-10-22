"""记忆健康车道：整表归因、极性三分类、来源包含失效、裁决后才归档。"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import yaml

from rsi_boot.api.tools.memory_health_tool import handle
from rsi_boot.learning.governance import mark_low_confidence
from rsi_boot.learning.memory_health import (
    MemoryHealth,
    demote_for_health,
)
from rsi_boot.learning.proposal_engine import ProposalEngine
from rsi_boot.memory.graph import load_catalog
from rsi_boot.memory.logstore import append_event
from rsi_boot.memory.paths import official_dir, pending_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.scheduler.manager import SchedulerManager
from rsi_boot.services.decision_queue import DecisionQueue, collect_decision_cards
from rsi_boot.services.knowledge_service import KnowledgeService
from rsi_boot.services.recall_service import RecallService


def _doc_id(n: int) -> str:
    # 文件名只用 id 前 8 位，序号必须落在这 8 位里，避免同标题互相覆盖。
    return f"{n:08x}{'a' * 24}"


def _write(
    store: MemoryStore, *, n: int, type: str, title: str,
    content: str = "正文足够说明这条记忆", extra: dict | None = None,
    refs: list[str] | None = None, pending: bool = False,
) -> str:
    doc_id = _doc_id(n)
    doc = MemoryDoc(
        id=doc_id, type=type, title=title, content=content,
        extra=extra or {}, refs=refs or [],
    )
    folder = pending_dir(store.rsi_dir, type) if pending else official_dir(store.rsi_dir, type)
    store.write(doc, dest=folder / memory_filename(title, doc_id))
    return doc_id


def _reject(
    store: MemoryStore, *, token: str, retrieved: list[str], comment: str,
    action: str = "rejected", rating: int | None = None, hours: int = 0,
) -> None:
    moment = datetime.now(timezone.utc) + timedelta(hours=hours)
    event = {
        "id": uuid.uuid4().hex,
        "ts": moment.isoformat(),
        "kind": "recall",
        "token": token,
        "action": action,
        "comment": comment,
        "retrieved": retrieved,
        "project_id": "p",
        "task": "任务",
        "arm": "default",
    }
    if rating is not None:
        event["rating"] = rating
    append_event(store.rsi_dir, event)


def _health(store: MemoryStore) -> MemoryHealth:
    return MemoryHealth(store)


def _only(store: MemoryStore) -> dict:
    rows = _health(store).list_findings("open")["findings"]
    assert len(rows) == 1
    return _health(store).explain(rows[0]["id"])["finding"]


def test_contradicting_comment_opens_revise_and_second_recommends_archive(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, n=1, type="prohibition", title="禁止跳过校验")
    _reject(store, token="once", retrieved=[doc_id], comment="可以跳过检查")
    first = _health(store).scan(tmp_path, project_id="p")
    assert first["opened"] == 1
    finding = _only(store)
    assert finding["kind"] == "incorrect"
    assert finding["recommended"] == "revise"
    _reject(store, token="twice", retrieved=[doc_id], comment="允许跳过检查")
    second = _health(store).scan(tmp_path, project_id="p")
    assert second["refreshed"] == 1
    again = _only(store)
    assert again["recommended"] == "archive"
    assert len(again["evidence"]) == 2


def test_same_polarity_and_empty_comment_do_not_open(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    ban = _write(store, n=1, type="prohibition", title="禁止跳过校验")
    tip = _write(store, n=2, type="convention", title="先写测试")
    _reject(store, token="ban", retrieved=[ban], comment="不要再跳过")
    _reject(store, token="tip", retrieved=[tip], comment="可以继续这样做")
    _reject(store, token="empty", retrieved=[ban], comment="")
    stats = _health(store).scan(tmp_path, project_id="p")
    assert stats["opened"] == 0


def test_neutral_needs_three_and_never_recommends_archive(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, n=1, type="convention", title="先写测试")
    _reject(store, token="n1", retrieved=[doc_id], comment="看情况再说")
    _reject(store, token="n2", retrieved=[doc_id], comment="看情况再说")
    assert _health(store).scan(tmp_path)["opened"] == 0
    _reject(store, token="n3", retrieved=[doc_id], comment="看情况再说")
    assert _health(store).scan(tmp_path)["opened"] == 1
    finding = _only(store)
    assert finding["recommended"] == "revise"


def test_feedback_is_attributed_to_every_retrieved_id(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    left = _write(store, n=1, type="convention", title="左侧做法")
    right = _write(store, n=2, type="convention", title="右侧做法")
    _reject(store, token="both", retrieved=[left, right], comment="不要再这样写")
    stats = _health(store).scan(tmp_path)
    assert stats["opened"] == 2
    ids = {row["memory_id"] for row in _health(store).list_findings()["findings"]}
    assert ids == {left, right}


def test_keep_ignores_old_evidence_and_reopens_on_newer_contradiction(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, n=1, type="convention", title="先写测试")
    _reject(store, token="old", retrieved=[doc_id], comment="不要再这样写", hours=-2)
    _health(store).scan(tmp_path)
    finding_id = _only(store)["id"]
    kept = _health(store).resolve(finding_id, "keep")
    assert kept["state"] == "resolved"
    assert _health(store).scan(tmp_path)["opened"] == 0
    assert _health(store).explain(finding_id)["finding"]["status"] == "resolved"
    _reject(store, token="new", retrieved=[doc_id], comment="不要再这样写", hours=1)
    assert _health(store).scan(tmp_path)["opened"] == 1
    reopened = _health(store).explain(finding_id)["finding"]
    assert reopened["status"] == "open"
    assert [row["token"] for row in reopened["evidence"]] == ["new"]


def test_stale_source_uses_phrase_containment_not_mere_age(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    docs = tmp_path / "docs"
    docs.mkdir()
    guide = docs / "guide.md"
    guide.write_text("这里完全没有那句标题", encoding="utf-8")
    changed = _write(
        store, n=1, type="documentation", title="使用本地缓存",
        extra={"source_url": "docs/guide.md", "source_hash": "0" * 64},
    )
    present = docs / "kept.md"
    present.write_text("请使用本地缓存来做这件事", encoding="utf-8")
    _write(
        store, n=2, type="documentation", title="使用本地缓存",
        extra={"source_url": "docs/kept.md", "source_hash": "0" * 64},
    )
    untouched = docs / "old.md"
    untouched.write_text("没有短语", encoding="utf-8")
    old = datetime.now().timestamp() - 86400
    os.utime(untouched, (old, old))
    _write(
        store, n=3, type="documentation", title="使用本地缓存",
        extra={"source_url": "docs/old.md"},
    )
    missing = _write(
        store, n=4, type="documentation", title="使用本地缓存",
        extra={"source_url": "docs/gone.md"},
    )
    _write(store, n=5, type="convention", title="没有来源的约定")
    stats = _health(store).scan(tmp_path)
    assert stats["opened"] == 2
    by_mem = {row["memory_id"]: row for row in _health(store).list_findings()["findings"]}
    assert set(by_mem) == {changed, missing}
    assert _health(store).explain(by_mem[changed]["id"])["finding"]["source_state"] == "phrase_absent"
    assert _health(store).explain(by_mem[missing]["id"])["finding"]["source_state"] == "missing"


def test_archive_moves_memory_and_links_successor_from_evidence_time(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, n=1, type="convention", title="先写测试")
    _reject(store, token="hit", retrieved=[doc_id], comment="不要再这样写", hours=-1)
    successor = _write(
        store, n=2, type="prohibition", title="禁止跳过测试",
        refs=[doc_id], pending=True,
    )
    _health(store).scan(tmp_path)
    finding_id = _only(store)["id"]
    result = _health(store).resolve(finding_id, "archive")
    assert result["status"] == "success"
    assert result["archived_id"] == doc_id
    assert result["successor_id"] == successor
    assert store.read(doc_id).status == "archived"
    edges = load_catalog(store.rsi_dir)["edges"]
    assert any(
        edge.get("from") == successor and edge.get("to") == doc_id and edge.get("rel") == "supersedes"
        for edge in edges
    )
    again = _health(store).resolve(finding_id, "archive")
    assert again["code"] == "invalid"


def test_archive_of_missing_memory_conflicts_and_closes(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, n=1, type="convention", title="先写测试")
    _reject(store, token="hit", retrieved=[doc_id], comment="不要再这样写")
    _health(store).scan(tmp_path)
    finding_id = _only(store)["id"]
    store.move(doc_id, store.rsi_dir / "memory" / "archive")
    result = _health(store).resolve(finding_id, "archive")
    assert result["code"] == "conflict"
    assert result["state"] == "resolved"


def test_revise_then_knowledge_update_clears_low_confidence(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, n=1, type="convention", title="先写测试")
    _reject(store, token="hit", retrieved=[doc_id], comment="不要再这样写")
    _health(store).scan(tmp_path)
    finding_id = _only(store)["id"]
    assert _health(store).resolve(finding_id, "revise")["state"] == "awaiting_edit"
    mark_low_confidence(store, [doc_id], "rejected")
    import asyncio
    updated = asyncio.run(KnowledgeService(store).update(doc_id, {"content": "先写失败的测试再改实现"}))
    assert updated["status"] == "success"
    extra = store.read(doc_id).extra
    assert "low_confidence" not in extra
    assert extra["low_confidence_count"] == 1
    closed = _health(store).explain(finding_id)["finding"]
    assert closed["status"] == "resolved"
    assert closed["resolution"] == "revise"


def test_update_without_awaiting_edit_keeps_low_confidence(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, n=1, type="convention", title="先写测试")
    mark_low_confidence(store, [doc_id], "rejected")
    import asyncio
    asyncio.run(KnowledgeService(store).update(doc_id, {"content": "先写失败的测试再改实现"}))
    assert store.read(doc_id).extra["low_confidence"] is True


def test_consumed_contradiction_is_not_copied_into_proposal_comments(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, n=1, type="convention", title="先写测试")
    _reject(store, token="held", retrieved=[doc_id], comment="不要再这样写")
    _reject(store, token="free", retrieved=[], comment="另写一条禁止项说明")
    _health(store).scan(tmp_path, project_id="p")
    buckets = ProposalEngine(store=store, config={})._mine_store_failures("p", 7)
    comments = [item for bucket in buckets for item in bucket.comments]
    assert "不要再这样写" not in comments
    assert "另写一条禁止项说明" in comments


def test_health_card_waits_behind_an_open_version_conflict(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, n=1, type="convention", title="先写测试")
    _reject(store, token="hit", retrieved=[doc_id], comment="不要再这样写")
    _health(store).scan(tmp_path)
    path = store.rsi_dir / "state" / "conflicts.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"conflicts": [{
        "id": _doc_id(9),
        "status": "open",
        "type": "version",
        "item_id": doc_id,
        "user_rule_path": "docs/new.md",
        "excerpt": "version",
    }]}, allow_unicode=True), encoding="utf-8")
    import asyncio
    cards = asyncio.run(collect_decision_cards(store, "p", project_root=tmp_path))
    assert {card.kind for card in cards} >= {"daily_conflict", "memory_health"}
    assert DecisionQueue().pick(cards).kind == "daily_conflict"


def test_demote_orders_plain_then_low_confidence_then_open():
    plain = MemoryDoc(id=_doc_id(1), type="convention", title="甲", content="甲的正文")
    low = MemoryDoc(
        id=_doc_id(2), type="convention", title="乙", content="乙的正文",
        extra={"low_confidence": True},
    )
    sick = MemoryDoc(id=_doc_id(3), type="convention", title="丙", content="丙的正文")
    index = {sick.id: {"id": _doc_id(4), "status": "open", "kind": "incorrect", "recommended": "revise"}}
    ordered = demote_for_health([sick, low, plain], index)
    assert [doc.id for doc in ordered] == [plain.id, low.id, sick.id]


async def test_recall_attaches_health_and_demotes(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    shared = "健康排序专用词条"
    healthy = _write(store, n=1, type="convention", title="健康的一条", content=shared)
    sick = _write(store, n=2, type="convention", title="生病的一条", content=shared)
    _reject(store, token="hit", retrieved=[sick], comment="不要再这样写")
    _health(store).scan(tmp_path)
    out = await RecallService(store=store, feedback_secret="s", project_root=tmp_path).recall(shared, "p")
    ids = [row["id"] for row in out["items"]]
    assert healthy in ids and sick in ids
    assert ids.index(healthy) < ids.index(sick)
    sick_row = next(row for row in out["items"] if row["id"] == sick)
    assert sick_row["health"]["kind"] == "incorrect"
    assert sick_row["health"]["status"] == "open"


async def test_tool_explain_is_read_only_and_resolve_notifies(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, n=1, type="convention", title="先写测试")
    _reject(store, token="hit", retrieved=[doc_id], comment="不要再这样写")

    class Runtime:
        project_id = "p"
        decisions = DecisionQueue()
        knowledge = KnowledgeService(store)

        def __init__(self) -> None:
            self.store = store
            self._project_root = tmp_path

    runtime = Runtime()
    scanned = await handle(runtime, {"action": "scan"})
    assert scanned["opened"] == 1
    finding_id = scanned["finding_ids"][0]
    explained = await handle(runtime, {"action": "explain", "finding_id": finding_id})
    assert explained["status"] == "success"
    assert len(explained["options"]) == 4
    assert _health(store).explain(finding_id)["finding"]["status"] == "open"
    resolved = await handle(runtime, {
        "action": "resolve", "finding_id": finding_id, "resolution": "snooze",
    })
    assert resolved["state"] == "snoozed"
    assert finding_id in runtime.decisions._closed
    missing = await handle(runtime, {"action": "explain", "finding_id": "0" * 32})
    assert missing["code"] == "not_found"
    bad = await handle(runtime, {"action": "list", "status": "nope"})
    assert bad["code"] == "invalid"


async def test_scheduler_scan_uses_project_root(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc_id = _write(store, n=1, type="convention", title="先写测试")
    _reject(store, token="hit", retrieved=[doc_id], comment="不要再这样写")
    sched = SchedulerManager(
        tmp_path / "archive", store=store, project_root=tmp_path, project_ids=["p"],
    )
    await sched._scan_memory_health()
    assert _health(store).list_findings()["findings"][0]["memory_id"] == doc_id
