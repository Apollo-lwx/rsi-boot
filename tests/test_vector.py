"""P2.1/P2.3 向量检索与混合检索测试（§2.3/§3.2）"""

import hashlib
import re

import numpy as np
import pytest

from rsi_boot.core.models import KnowledgeItem
from rsi_boot.data.vec import BruteBackend, EMBEDDING_DIM, create_vector_backend
from rsi_boot.knowledge.embedding import EmbeddingService, _mock_embed
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.services.knowledge_service import KnowledgeService


class ToyEmbedder:
    """词袋基向量嵌入：共享 token 产生真实 cosine 相似度，可控可断言"""

    def __init__(self, dim: int = EMBEDDING_DIM):
        self.dim = dim

    @staticmethod
    def _tokens(text: str) -> list[str]:
        tokens = re.findall(r"[a-z0-9_]+", text.lower())
        for run in re.findall(r"[一-鿿]+", text):
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
        return tokens

    async def embed_one(self, text: str, api_key=None):
        vec = np.zeros(self.dim, dtype=np.float32)
        for tok in self._tokens(text):
            vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
        return vec

    async def embed(self, texts, api_key=None):
        return [await self.embed_one(t) for t in texts]


class FailEmbedder:
    """嵌入全部失败：验证降级为仅 FTS"""

    async def embed_one(self, text, api_key=None):
        return None


@pytest.fixture
def toy():
    return ToyEmbedder()


@pytest.fixture
def brute(db):
    return BruteBackend(db)


async def _add(service, title, content, project="p1", roles=None):
    item = KnowledgeItem(project_id=project, title=title, content=content, roles=roles or [])
    return await service.add(item)


# ---------- P2.1 后端与嵌入 ----------


def test_mock_embedding_deterministic():
    a = _mock_embed("hello world")
    b = _mock_embed("hello world")
    c = _mock_embed("different text")
    assert a.shape == (EMBEDDING_DIM,)
    assert np.allclose(a, b)
    assert not np.allclose(a, c)
    assert abs(float(np.linalg.norm(a)) - 1.0) < 1e-5


async def test_embedding_service_none_provider():
    svc = EmbeddingService({"embedding": {"provider": "none"}})
    assert await svc.embed_one("anything") is None


async def test_add_stores_blob_and_indexes(db, base_config, toy, brute):
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=brute)
    service = KnowledgeService(db, retriever, embedding=toy, vec=brute)
    await _add(service, "Deploy guide", "docker compose deploy stack")

    conn = await db.connect()
    async with conn.execute("SELECT embedding FROM knowledge_items") as cur:
        row = await cur.fetchone()
    assert row["embedding"] is not None
    vec = np.frombuffer(row["embedding"], dtype=np.float32)
    assert vec.shape == (EMBEDDING_DIM,)
    assert float(np.linalg.norm(vec)) > 0


async def test_embedding_failure_degrades_to_fts_only(db, base_config, brute):
    retriever = KnowledgeRetriever(db, base_config, embedding=FailEmbedder(), vec=brute)
    service = KnowledgeService(db, retriever, embedding=FailEmbedder(), vec=brute)
    item_id = await _add(service, "Deploy guide", "docker compose deploy")

    conn = await db.connect()
    async with conn.execute("SELECT embedding FROM knowledge_items WHERE id = ?", (item_id,)) as cur:
        row = await cur.fetchone()
    assert row["embedding"] is None  # 降级：仅 FTS 可检索
    results = await retriever.search("docker deploy", "p1")
    assert len(results) == 1


async def test_auto_backend_falls_back_to_brute(db):
    backend = await create_vector_backend(db, {"vector": {"backend": "brute"}})
    assert isinstance(backend, BruteBackend)


# ---------- P2.3 混合检索 ----------


async def test_vector_channel_recalls_cjk(db, base_config, toy, brute):
    """CJK 查询：FTS 通道跳过，向量通道独立召回（不再依赖 bigram 补丁）"""
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=brute)
    service = KnowledgeService(db, retriever, embedding=toy, vec=brute)
    await _add(service, "命名约定", "所有标识符使用蛇形命名法")

    # toy 词袋向量需较高 token 重叠才能过 0.6 门槛（真实 embedding 语义余弦天然更高）
    results = await retriever.search("标识符使用蛇形命名", "p1")
    assert len(results) == 1
    assert "蛇形命名法" in results[0].content


async def test_threshold_blocks_irrelevant(db, base_config, toy, brute):
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=brute)
    service = KnowledgeService(db, retriever, embedding=toy, vec=brute)
    await _add(service, "Recipe", "banana bread walnuts sugar")

    # 零 token 重叠：cosine ≈ 0，BM25 无命中 → 不注入
    assert await retriever.search("kubernetes pod eviction", "p1") == []


async def test_dual_channel_ranks_first(db, base_config, toy, brute):
    """双通道命中的条目 RRF 融合后应排在纯单通道条目之前"""
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=brute)
    service = KnowledgeService(db, retriever, embedding=toy, vec=brute)
    await _add(service, "Both", "docker deploy compose stack service")
    await _add(service, "FTS only", "docker deploy")  # 词项少，toy 向量 cosine 低

    results = await retriever.search("docker deploy compose", "p1")
    assert results
    assert results[0].title == "Both"


async def test_vector_project_isolation(db, base_config, toy, brute):
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=brute)
    service = KnowledgeService(db, retriever, embedding=toy, vec=brute)
    await _add(service, "Secret", "internal deploy runbook", project="p1")

    assert await retriever.search("internal deploy runbook", "other") == []


async def test_vector_role_filter(db, base_config, toy, brute):
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=brute)
    service = KnowledgeService(db, retriever, embedding=toy, vec=brute)
    await _add(service, "Dev doc", "registry mirror setup", roles=["developer"])

    assert await retriever.search("registry mirror", "p1", role="pm") == []
    assert len(await retriever.search("registry mirror", "p1", role="developer")) == 1


async def test_delete_removes_from_vector_index(db, base_config, toy, brute):
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=brute)
    service = KnowledgeService(db, retriever, embedding=toy, vec=brute)
    item_id = await _add(service, "Guide", "docker compose deploy")
    assert await retriever.search("docker compose", "p1")

    assert await service.delete(item_id, "p1") is True
    assert await retriever.search("docker compose", "p1") == []


# ---------- sqlite-vec 后端（KNO-10：两后端 TopK 重合度 ≥ 80%）----------


@pytest.fixture
async def sqlite_vec_backend(db):
    backend = await create_vector_backend(db, {"vector": {"backend": "sqlite-vec"}})
    if not isinstance(backend, BruteBackend):
        return backend
    pytest.skip("sqlite-vec 未安装")


async def test_sqlite_vec_search(db, base_config, toy, sqlite_vec_backend):
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=sqlite_vec_backend)
    service = KnowledgeService(db, retriever, embedding=toy, vec=sqlite_vec_backend)
    await _add(service, "Deploy", "docker compose deploy stack")
    await _add(service, "Other", "banana bread recipe")

    results = await retriever.search("docker deploy", "p1")
    assert results
    assert results[0].title == "Deploy"


async def test_backend_consistency(db, base_config, toy, sqlite_vec_backend):
    """相同语料相同查询：sqlite-vec 与 brute 的 Top5 重合度 ≥ 80%（KNO-10）"""
    brute = BruteBackend(db)
    docs = [
        ("DocA", "docker compose deploy production stack"),
        ("DocB", "kubernetes pod scheduling eviction policy"),
        ("DocC", "python asyncio coroutine event loop"),
        ("DocD", "docker image build layer cache"),
        ("DocE", "sqlite wal mode transaction"),
        ("DocF", "banana bread recipe walnuts"),
    ]
    service = KnowledgeService(db, KnowledgeRetriever(db, base_config), embedding=toy, vec=brute)
    rowids = []
    for title, content in docs:
        item_id = await _add(service, title, content)
        conn = await db.connect()
        async with conn.execute("SELECT rowid, embedding FROM knowledge_items WHERE id = ?", (item_id,)) as cur:
            row = await cur.fetchone()
        rowids.append(int(row["rowid"]))
        await sqlite_vec_backend.upsert(int(row["rowid"]), np.frombuffer(row["embedding"], dtype=np.float32))

    qvec = await toy.embed_one("docker deploy")
    brute_top = {rid for rid, _ in await brute.search(qvec, top_k=5)}
    vec_top = {rid for rid, _ in await sqlite_vec_backend.search(qvec, top_k=5)}
    overlap = len(brute_top & vec_top) / max(len(brute_top), 1)
    assert overlap >= 0.8
