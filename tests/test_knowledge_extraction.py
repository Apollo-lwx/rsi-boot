"""P3.4 知识提取测试（§4.3）：候选汇集 → LLM 提取 → 去重 → pending_review → review 流转"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from rsi_boot.core.models import KnowledgeItem
from rsi_boot.feedback.implicit_tracker import FeedbackWorker, ImplicitEvent
from rsi_boot.knowledge.embedding import EmbeddingService
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.learning.knowledge_extractor import KnowledgeExtractor
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
        # LLM 提取 + 向量去重为附录 C 可选增强
        config["enhance"] = {"extract_llm": True, "model": {"default": "mock"}}
    return config


def _services(db, config):
    embedding = EmbeddingService(config)
    adapter = ModelAdapter(ModelRegistry(config), config)
    extractor = KnowledgeExtractor(db, embedding, adapter, config)
    retriever = KnowledgeRetriever(db, config, embedding=embedding)
    knowledge = KnowledgeService(db, retriever, embedding=embedding)
    return extractor, knowledge, adapter


async def _make_log(db, logs: LogService, rating=None, with_excerpt=True):
    """构造一条终态日志，返回 (log_id, feedback_token)"""
    from rsi_boot.core.models import RSIRequest

    req = RSIRequest(user_id="u1", project_id="p1", raw_input="如何配置缓存？")
    token = "tok-" + uuid.uuid4().hex[:8]
    log_id = await logs.insert_pending(req, token)
    await logs.finalize(
        log_id, status="success", intent="explain",
        response_excerpt="使用 TTL 缓存，容量 500，过期 300 秒。" if with_excerpt else None,
    )
    if rating is not None:
        await logs.apply_feedback(token, "accepted", rating)
    return log_id, token


# ---------- 候选汇集 ----------


async def test_collect_high_rated(db):
    config = _config()
    extractor, _, _ = _services(db, config)
    logs = LogService(db)
    await _make_log(db, logs, rating=5)
    await _make_log(db, logs, rating=2)          # 低分不入候选
    await _make_log(db, logs, rating=4, with_excerpt=False)  # 无摘录不入候选

    assert await extractor.collect_high_rated() == 1
    # 幂等：重复汇集不新增
    assert await extractor.collect_high_rated() == 0


async def test_modified_feedback_enqueues_candidate(db):
    config = _config()
    logs = LogService(db)
    log_id, token = await _make_log(db, logs)
    worker = FeedbackWorker(db)
    # diff 比例 > 20% 的修改
    await worker._process(ImplicitEvent(
        feedback_token=token, action="modified",
        modified_content="完全不同的内容，使用 LRU 缓存并设置容量 1000，过期 600 秒，加互斥锁防击穿。" * 3,
    ))
    conn = await db.connect()
    async with conn.execute(
        "SELECT candidate_type, question, answer FROM extraction_candidates WHERE source_log_id = ?",
        (log_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["candidate_type"] == "modified"
    assert "LRU" in row["answer"]


# ---------- LLM 提取与去重 ----------


def _fake_extract_adapter(adapter, monkeypatch, draft=None):
    async def fake_complete(model, messages):
        payload = draft if draft is not None else {
            "title": "缓存配置经验", "content": "TTL 缓存容量 500、过期 300 秒可平衡命中与新鲜度。",
            "tags": ["缓存"], "domain": "backend", "roles": [],
        }
        return ModelResult(text=json.dumps(payload, ensure_ascii=False), model=model,
                           prompt_tokens=1, completion_tokens=1, latency_ms=1)

    monkeypatch.setattr(adapter, "complete", fake_complete)


async def test_run_daily_extracts_to_pending_review(db, monkeypatch):
    config = _config(llm=True)
    extractor, knowledge, adapter = _services(db, config)
    logs = LogService(db)
    await _make_log(db, logs, rating=5)
    _fake_extract_adapter(adapter, monkeypatch)

    stats = await extractor.run_daily()
    assert stats["collected"] == 1
    assert stats["extracted"] == 1

    items = await knowledge.list("p1")
    assert len(items) == 1
    assert items[0]["status"] == "pending_review"
    assert items[0]["title"] == "缓存配置经验"
    # pending_review 不生成 embedding（确认后才生成）
    conn = await db.connect()
    async with conn.execute("SELECT embedding FROM knowledge_items WHERE id = ?", (items[0]["id"],)) as cur:
        assert (await cur.fetchone())["embedding"] is None


async def test_run_daily_skip_valueless(db, monkeypatch):
    config = _config(llm=True)
    extractor, knowledge, adapter = _services(db, config)
    logs = LogService(db)
    await _make_log(db, logs, rating=5)
    _fake_extract_adapter(adapter, monkeypatch, draft={"skip": True})

    stats = await extractor.run_daily()
    assert stats["skipped"] == 1
    assert await knowledge.list("p1") == []


async def test_dedup_merges_similar(db, monkeypatch):
    """cosine ≥ 0.9 的草稿合并入现有条目（刷新 updated_at），不产生新条目"""
    config = _config(llm=True)
    extractor, knowledge, adapter = _services(db, config)
    # 已有条目（mock embedding 由文本哈希播种，同文本向量相同 → cosine = 1.0）
    existing_id = await knowledge.add(KnowledgeItem(
        project_id="p1", title="缓存配置经验", content="TTL 缓存容量 500、过期 300 秒可平衡命中与新鲜度。",
    ))
    conn = await db.connect()
    async with conn.execute("SELECT updated_at FROM knowledge_items WHERE id = ?", (existing_id,)) as cur:
        old_updated = (await cur.fetchone())["updated_at"]

    logs = LogService(db)
    await _make_log(db, logs, rating=5)
    _fake_extract_adapter(adapter, monkeypatch)  # 草稿 title/content 与已有条目相同

    stats = await extractor.run_daily()
    assert stats["merged"] == 1
    assert stats["extracted"] == 0
    items = await knowledge.list("p1")
    assert len(items) == 1  # 未产生新条目


# ---------- 规则式提取（v3.0 默认路径，零 Key，§4.4） ----------


async def test_rule_extract_high_rated_qa_pair(db):
    """high_rated 候选 → 问答对直接沉淀为 convention（规则式，无 LLM）"""
    config = _config()
    extractor, knowledge, _ = _services(db, config)
    logs = LogService(db)
    await _make_log(db, logs, rating=5)

    stats = await extractor.run_daily()
    assert stats["collected"] == 1 and stats["extracted"] == 1
    items = await knowledge.list("p1")
    assert len(items) == 1
    assert items[0]["status"] == "pending_review"
    assert items[0]["content_type"] == "convention"
    assert items[0]["title"] == "如何配置缓存？"
    conn = await db.connect()
    async with conn.execute(
        "SELECT content FROM knowledge_items WHERE id = ?", (items[0]["id"],)
    ) as cur:
        assert "TTL 缓存" in (await cur.fetchone())["content"]


async def test_rule_extract_rejected_comment_prohibition(db):
    """rejected+comment 候选 → 禁止项，标题前缀规范化「禁止：」"""
    config = _config()
    extractor, knowledge, _ = _services(db, config)
    conn = await db.connect()
    await conn.execute(
        "INSERT INTO extraction_candidates (id, project_id, source_log_id, candidate_type,"
        " question, answer, status, created_at)"
        " VALUES ('c1', 'p1', 'log1', 'rejected', '如何查询用户？', '不要用 SELECT *，必须显式列字段',"
        " 'pending', ?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    await conn.commit()

    stats = await extractor.run_daily()
    assert stats["extracted"] == 1
    items = await knowledge.list("p1")
    assert items[0]["content_type"] == "prohibition"
    assert items[0]["title"].startswith("禁止：")
    async with conn.execute(
        "SELECT content FROM knowledge_items WHERE id = ?", (items[0]["id"],)
    ) as cur:
        assert "SELECT *" in (await cur.fetchone())["content"]


async def test_rule_dedup_by_title(db):
    """规则式去重：标题完全匹配 → 合并刷新 updated_at，不产生新条目"""
    config = _config()
    extractor, knowledge, _ = _services(db, config)
    existing_id = await knowledge.add(KnowledgeItem(
        project_id="p1", title="如何配置缓存？", content="旧答案",
    ))
    logs = LogService(db)
    await _make_log(db, logs, rating=5)  # 规则式草稿标题 = 问题摘要 = 已有条目标题

    stats = await extractor.run_daily()
    assert stats["merged"] == 1 and stats["extracted"] == 0
    assert len(await knowledge.list("p1")) == 1


# ---------- 人工确认流转 ----------


async def test_review_approve_generates_embedding(db):
    config = _config()
    _, knowledge, _ = _services(db, config)
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    item_id = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, status, created_at, updated_at)"
        " VALUES (?, 'p1', '草稿', '内容', 'pending_review', ?, ?)",
        (item_id, now, now),
    )
    await conn.commit()

    assert await knowledge.review(item_id, "p1", approve=True) == "active"
    async with conn.execute(
        "SELECT status, embedding FROM knowledge_items WHERE id = ?", (item_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "active"
    assert row["embedding"] is not None  # 确认后生成 embedding


async def test_review_reject_and_cleanup(db):
    config = _config()
    extractor, knowledge, _ = _services(db, config)
    conn = await db.connect()
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    stale_id, fresh_id = uuid.uuid4().hex, uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, status, created_at, updated_at)"
        " VALUES (?, 'p1', 'a', 'a', 'pending_review', ?, ?), (?, 'p1', 'b', 'b', 'pending_review', ?, ?)",
        (stale_id, old, old, fresh_id, now, now),
    )
    await conn.commit()

    assert await knowledge.review(stale_id, "p1", approve=False) == "rejected"
    # rejected 超 30 天清理：stale 条目 updated_at 被刷新为现在，需手动回拨模拟
    await conn.execute("UPDATE knowledge_items SET updated_at = ? WHERE id = ?", (old, stale_id))
    await conn.commit()
    assert await extractor.cleanup_rejected() == 1
    # 非 pending_review 不可审
    assert await knowledge.review(stale_id, "p1", approve=True) is None
    assert await knowledge.review(fresh_id, "p1", approve=True) == "active"
