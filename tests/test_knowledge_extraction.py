"""P3.4 知识提取测试（§4.3）：候选汇集 → 规则提取 → pending YAML → review 流转"""

import json
import uuid
from datetime import datetime, timezone

from rsi_boot.core.models import RSIRequest
from rsi_boot.feedback.implicit_tracker import FeedbackWorker, ImplicitEvent
from rsi_boot.learning.knowledge_extractor import KnowledgeExtractor
from rsi_boot.memory.logstore import append_event
from rsi_boot.memory.paths import official_dir, pending_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.model.adapter import ModelAdapter
from rsi_boot.model.providers.base import ModelResult
from rsi_boot.model.registry import ModelRegistry
from rsi_boot.services.knowledge_service import KnowledgeService
from rsi_boot.services.log_service import LogService


def _config(llm: bool = False):
    config = {
        "model": {"default": "mock", "timeout_s": 5, "max_retries": 0},
        "retrieval": {"top_n": 5, "token_budget": 2000},
        "embedding": {"provider": "mock"},
    }
    if llm:
        config["enhance"] = {"extract_llm": True, "model": {"default": "mock"}}
    return config


def _services(store: MemoryStore, tmp_path):
    extractor = KnowledgeExtractor(store=store)
    knowledge = KnowledgeService(store=store, project_root=tmp_path)
    return extractor, knowledge


def _high_rated_event(
    store: MemoryStore,
    *,
    rating=5,
    excerpt="使用 TTL 缓存，容量 500，过期 300 秒。",
    task="如何配置缓存？",
    event_id=None,
):
    eid = event_id or uuid.uuid4().hex
    append_event(store.rsi_dir, {
        "id": eid,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "recall",
        "task": task,
        "excerpt": excerpt,
        "rating": rating,
        "retrieved": [],
    })
    return eid


# ---------- 候选汇集 ----------


async def test_collect_high_rated(store, tmp_path):
    extractor, _ = _services(store, tmp_path)
    _high_rated_event(store, rating=5)
    _high_rated_event(store, rating=2)
    _high_rated_event(store, rating=4, excerpt="")

    first = await extractor.run_daily()
    assert first["collected"] == 1
    assert first["extracted"] == 1
    second = await extractor.run_daily()
    assert second["collected"] == 0
    assert second["extracted"] == 0


async def test_modified_feedback_enqueues_candidate(store):
    logs = LogService(store)
    req = RSIRequest(user_id="u1", project_id="p1", raw_input="如何配置缓存？")
    token = "tok-" + uuid.uuid4().hex[:8]
    log_id = await logs.insert_pending(req, token)
    await logs.finalize(
        log_id, status="success", intent="explain",
        response_excerpt="使用 TTL 缓存，容量 500，过期 300 秒。",
    )
    worker = FeedbackWorker(store=store)
    await worker._process(ImplicitEvent(
        feedback_token=token, action="modified",
        modified_content="完全不同的内容，使用 LRU 缓存并设置容量 1000，过期 600 秒，加互斥锁防击穿。" * 3,
    ))
    pending = list((store.rsi_dir / "memory" / "pending" / "conventions").glob("*.yaml"))
    assert pending
    text = pending[0].read_text(encoding="utf-8")
    assert "LRU" in text
    cand = store.rsi_dir / "logs" / "candidates.jsonl"
    assert cand.is_file()
    rows = [json.loads(ln) for ln in cand.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows and rows[0]["candidate_type"] == "modified"


# ---------- LLM 提取（附录 C，仅 extract_draft） ----------


def _fake_extract_adapter(adapter, monkeypatch, draft=None):
    async def fake_complete(model, messages):
        payload = draft if draft is not None else {
            "title": "缓存配置经验", "content": "TTL 缓存容量 500、过期 300 秒可平衡命中与新鲜度。",
            "tags": ["缓存"], "domain": "backend", "roles": [],
        }
        return ModelResult(text=json.dumps(payload, ensure_ascii=False), model=model,
                           prompt_tokens=1, completion_tokens=1, latency_ms=1)

    monkeypatch.setattr(adapter, "complete", fake_complete)


async def test_run_daily_extracts_to_pending_review(store, tmp_path):
    extractor, knowledge = _services(store, tmp_path)
    _high_rated_event(store, rating=5)

    stats = await extractor.run_daily()
    assert stats["collected"] == 1
    assert stats["extracted"] == 1

    items = await knowledge.list("p1")
    assert len(items) == 1
    assert items[0]["status"] == "pending_review"
    assert items[0]["title"] == "如何配置缓存？"
    doc = store.read(items[0]["id"])
    assert (doc.path or "").replace("\\", "/").startswith("memory/pending/")


async def test_run_daily_skip_valueless(store, tmp_path, monkeypatch):
    extractor, knowledge = _services(store, tmp_path)
    _high_rated_event(store, rating=5, excerpt="")
    stats = await extractor.run_daily()
    assert stats["extracted"] == 0
    assert await knowledge.list("p1") == []

    config = _config(llm=True)
    adapter = ModelAdapter(ModelRegistry(config), config)
    llm = KnowledgeExtractor(store=store, adapter=adapter, config=config)
    _fake_extract_adapter(adapter, monkeypatch, draft={"skip": True})
    assert await llm.extract_draft("如何配置缓存？", "TTL") is None


async def test_dedup_merges_similar(store, tmp_path):
    """同一 source_event_id 不重复提取（文件运行时按 candidates.jsonl 幂等）"""
    extractor, knowledge = _services(store, tmp_path)
    eid = _high_rated_event(store, rating=5)
    first = await extractor.run_daily()
    assert first["extracted"] == 1
    _high_rated_event(store, rating=5, event_id=eid)
    second = await extractor.run_daily()
    assert second["extracted"] == 0
    assert second["merged"] == 0
    assert len(await knowledge.list("p1")) == 1


# ---------- 规则式提取（v3.0 默认路径，零 Key，§4.4） ----------


async def test_rule_extract_high_rated_qa_pair(store, tmp_path):
    """high_rated 候选 → 问答对直接沉淀为 convention（规则式，无 LLM）"""
    extractor, knowledge = _services(store, tmp_path)
    _high_rated_event(store, rating=5)

    stats = await extractor.run_daily()
    assert stats["collected"] == 1 and stats["extracted"] == 1
    items = await knowledge.list("p1")
    assert len(items) == 1
    assert items[0]["status"] == "pending_review"
    assert items[0]["content_type"] == "convention"
    assert items[0]["title"] == "如何配置缓存？"
    assert "TTL 缓存" in store.read(items[0]["id"]).content


async def test_rule_extract_rejected_comment_prohibition(store, tmp_path):
    """rejected+comment 候选 → 禁止项，标题前缀规范化「禁止：」"""
    extractor, knowledge = _services(store, tmp_path)
    extractor.extract_from_feedback(
        action="rejected",
        comment="不要用 SELECT *，必须显式列字段",
        question="如何查询用户？",
    )

    items = await knowledge.list("p1")
    assert len(items) == 1
    assert items[0]["content_type"] == "prohibition"
    assert items[0]["title"].startswith("禁止：")
    assert "SELECT *" in store.read(items[0]["id"]).content


async def test_rule_dedup_by_title(store, tmp_path):
    """同一高分事件再跑 run_daily → 不新增条目"""
    extractor, knowledge = _services(store, tmp_path)
    eid = _high_rated_event(store, rating=5, task="如何配置缓存？")
    first = await extractor.run_daily()
    assert first["extracted"] == 1
    _high_rated_event(store, rating=5, task="如何配置缓存？", event_id=eid)
    stats = await extractor.run_daily()
    assert stats["extracted"] == 0
    assert len(await knowledge.list("p1")) == 1


# ---------- 人工确认流转 ----------


async def test_review_approve_generates_embedding(store, tmp_path):
    knowledge = KnowledgeService(store=store, project_root=tmp_path)
    item_id = "a" * 32
    store.write(
        MemoryDoc(id=item_id, type="convention", title="草稿", content="内容足够长"),
        dest=pending_dir(store.rsi_dir, "convention") / memory_filename("草稿", item_id),
    )

    assert await knowledge.review(item_id, "p1", approve=True) == "active"
    doc = store.read(item_id)
    assert doc.status == "active"
    assert official_dir(store.rsi_dir, "convention") in (store.rsi_dir / (doc.path or ".")).parents


async def test_review_reject_and_cleanup(store, tmp_path):
    knowledge = KnowledgeService(store=store, project_root=tmp_path)
    stale_id, fresh_id = "b" * 32, "c" * 32
    store.write(
        MemoryDoc(id=stale_id, type="convention", title="a", content="旧草稿足够长"),
        dest=pending_dir(store.rsi_dir, "convention") / memory_filename("a", stale_id),
    )
    store.write(
        MemoryDoc(id=fresh_id, type="convention", title="b", content="新草稿足够长"),
        dest=pending_dir(store.rsi_dir, "convention") / memory_filename("b", fresh_id),
    )

    assert await knowledge.review(stale_id, "p1", approve=False) == "archived"
    assert store.read(stale_id).status == "archived"
    assert await knowledge.review(fresh_id, "p1", approve=True) == "active"
    assert await knowledge.review(fresh_id, "p1", approve=True) is None
