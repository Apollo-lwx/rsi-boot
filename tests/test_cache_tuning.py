"""P4.4：进程内缓存调优（§2.4）——检索结果缓存、embedding 缓存、响应缓存（默认关闭）。"""

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from rsi_boot.core.models import KnowledgeItem, RSIRequest
from rsi_boot.knowledge.embedding import EmbeddingService
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.model.adapter import ModelAdapter
from rsi_boot.model.providers.mock import MockProvider
from rsi_boot.orchestrator.pipeline import Pipeline
from rsi_boot.services.knowledge_service import KnowledgeService


def _retriever(db, config):
    return KnowledgeRetriever(db, config)  # 无 embedding/vec → 纯 FTS 通道


async def _add_item(service, title, content, project_id="p1"):
    return await service.add(KnowledgeItem(project_id=project_id, title=title, content=content))


# ---------- 检索结果缓存 ----------


async def test_retrieval_cache_hit_and_invalidate_on_write(db, base_config):
    retriever = _retriever(db, base_config)
    service = KnowledgeService(db, retriever)
    await _add_item(service, "timeout guide", "configure timeout properly")

    first = await retriever.search("timeout", "p1")
    assert len(first) == 1

    # 绕过 service 直接插库（不失效缓存）→ 命中旧缓存，看不到新条目
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, roles, tags, status, created_at, updated_at) "
        "VALUES (?, 'p1', 'timeout extra', 'timeout advanced tuning', 'documentation', '[]', '[]', 'active', ?, ?)",
        (uuid.uuid4().hex, now, now),
    )
    await conn.commit()
    assert len(await retriever.search("timeout", "p1")) == 1  # 缓存命中

    # 经 service 写入 → invalidate_cache → 立即可见
    await _add_item(service, "timeout more", "timeout retry backoff")
    assert len(await retriever.search("timeout", "p1")) == 3


async def test_retrieval_cache_invalidate_on_delete(db, base_config):
    retriever = _retriever(db, base_config)
    service = KnowledgeService(db, retriever)
    item_id = await _add_item(service, "timeout guide", "configure timeout properly")
    assert len(await retriever.search("timeout", "p1")) == 1

    await service.delete(item_id, "p1")
    assert await retriever.search("timeout", "p1") == []  # 删除后缓存已失效


async def test_retrieval_cache_disabled(db, base_config):
    cfg = {**base_config, "retrieval": {**base_config["retrieval"], "cache_ttl_s": 0}}
    retriever = _retriever(db, cfg)
    service = KnowledgeService(db, retriever)
    await _add_item(service, "timeout guide", "configure timeout properly")
    assert len(await retriever.search("timeout", "p1")) == 1

    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, roles, tags, status, created_at, updated_at) "
        "VALUES (?, 'p1', 'timeout extra', 'timeout advanced tuning', 'documentation', '[]', '[]', 'active', ?, ?)",
        (uuid.uuid4().hex, now, now),
    )
    await conn.commit()
    assert len(await retriever.search("timeout", "p1")) == 2  # 无缓存，直接回源


# ---------- embedding 结果缓存 ----------


class _FakeEmbeddings:
    def __init__(self):
        self.calls = 0
        self.batches = []

    async def create(self, model, input):
        self.calls += 1
        self.batches.append(list(input))
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * 8) for _ in input])


def _openai_service(**emb_cfg):
    svc = EmbeddingService({"embedding": {"provider": "openai", "model": "m", **emb_cfg}})
    svc._client = SimpleNamespace(embeddings=_FakeEmbeddings())
    return svc


async def test_embedding_cache_hit(db, base_config):
    svc = _openai_service()
    fake = svc._client.embeddings

    await svc.embed(["alpha", "beta"], api_key="k")
    assert fake.calls == 1

    results = await svc.embed(["alpha", "gamma"], api_key="k")
    assert fake.calls == 2
    assert fake.batches[1] == ["gamma"]  # alpha 命中缓存，仅 miss 回源
    assert results[0] is not None and results[1] is not None


async def test_embedding_cache_disabled(db, base_config):
    svc = _openai_service(cache_ttl_s=0)
    fake = svc._client.embeddings
    await svc.embed(["alpha"], api_key="k")
    await svc.embed(["alpha"], api_key="k")
    assert fake.calls == 2


async def test_embedding_failure_not_cached(db, base_config):
    svc = _openai_service()
    fake = svc._client.embeddings

    async def _boom(model, input):
        raise RuntimeError("api down")

    fake.create = _boom
    assert (await svc.embed(["alpha"], api_key="k")) == [None]  # 失败不抛异常

    fake.create = lambda model, input: _FakeEmbeddings().create(model, input)
    result = await svc.embed(["alpha"], api_key="k")  # 失败未缓存，恢复后重试成功
    assert result[0] is not None


# ---------- 响应缓存（默认关闭） ----------


class _StubRegistry:
    def __init__(self, provider):
        self._provider = provider

    def get_provider(self, model_name):
        return self._provider


class _CountingMock(MockProvider):
    def __init__(self):
        self.calls = 0

    async def complete(self, model, messages, timeout_s):
        self.calls += 1
        return await super().complete(model, messages, timeout_s)


def _pipeline(db, config, provider):
    adapter = ModelAdapter(_StubRegistry(provider), config)
    return Pipeline(db, config, KnowledgeRetriever(db, config), adapter)


async def test_response_cache_hit(db, base_config):
    cfg = {**base_config, "response_cache": {"enabled": True}}
    provider = _CountingMock()
    pipeline = _pipeline(db, cfg, provider)

    req1 = RSIRequest(user_id="u", raw_input="如何配置超时", intent="howto")
    req2 = RSIRequest(user_id="u", raw_input="如何配置超时", intent="howto")
    resp1 = await pipeline.run(req1)
    resp2 = await pipeline.run(req2)

    assert provider.calls == 1  # 第二次未调模型
    assert resp1.status == resp2.status == "success"
    assert resp1.content[0].body == resp2.content[0].body
    assert resp2.content[0].structured_data == {"cache_hit": True}
    assert resp1.feedback_token != resp2.feedback_token  # 各自可独立反馈

    conn = await db.connect()
    async with conn.execute(
        "SELECT total_tokens FROM interaction_logs ORDER BY created_at"
    ) as cur:
        rows = await cur.fetchall()
    assert [r["total_tokens"] for r in rows][1] == 0  # 命中行零 token，不污染用量统计


async def test_response_cache_disabled_by_default(db, base_config):
    provider = _CountingMock()
    pipeline = _pipeline(db, base_config, provider)
    for _ in range(2):
        await pipeline.run(RSIRequest(user_id="u", raw_input="如何配置超时", intent="howto"))
    assert provider.calls == 2


async def test_response_cache_key_covers_input(db, base_config):
    cfg = {**base_config, "response_cache": {"enabled": True}}
    provider = _CountingMock()
    pipeline = _pipeline(db, cfg, provider)
    await pipeline.run(RSIRequest(user_id="u", raw_input="如何配置超时", intent="howto"))
    await pipeline.run(RSIRequest(user_id="u", raw_input="如何配置缓存", intent="howto"))
    assert provider.calls == 2  # 请求特征不同 → 不命中
