"""P2.1/P2.3 向量检索与混合检索测试（§2.3/§3.2）"""

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

import numpy as np
import pytest

from rsi_boot.data.vec import BruteBackend, EMBEDDING_DIM, create_vector_backend
from rsi_boot.knowledge.embedding import EmbeddingService, _mock_embed
from rsi_boot.knowledge.retriever import KnowledgeRetriever


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


async def _add(db, title, content, project="p1", roles=None, embedding=None, vec=None):
    item_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    blob = None
    arr = embedding
    if arr is not None:
        arr = np.asarray(arr, dtype=np.float32)
        blob = arr.tobytes()
    conn = await db.connect()
    await conn.execute(
        "INSERT INTO knowledge_items (id, project_id, title, content, content_type, roles, tags, status, embedding, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'documentation', ?, '[]', 'active', ?, ?, ?)",
        (item_id, project, title, content, json.dumps(roles or []), blob, now, now),
    )
    await conn.commit()
    if vec is not None and arr is not None:
        async with conn.execute("SELECT rowid FROM knowledge_items WHERE id = ?", (item_id,)) as cur:
            row = await cur.fetchone()
        await vec.upsert(int(row["rowid"]), arr)
    return item_id


async def _embed_add(db, embedder, title, content, project="p1", roles=None, vec=None):
    vec_arr = await embedder.embed_one(f"{title}\n{content}")
    return await _add(db, title, content, project=project, roles=roles, embedding=vec_arr, vec=vec)


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
    item_id = await _embed_add(db, toy, "Deploy guide", "docker compose deploy stack", vec=brute)

    conn = await db.connect()
    async with conn.execute("SELECT embedding FROM knowledge_items WHERE id = ?", (item_id,)) as cur:
        row = await cur.fetchone()
    assert row["embedding"] is not None
    vec = np.frombuffer(row["embedding"], dtype=np.float32)
    assert vec.shape == (EMBEDDING_DIM,)
    assert float(np.linalg.norm(vec)) > 0
    assert await retriever.search("docker deploy", "p1")


async def test_embedding_failure_degrades_to_fts_only(db, base_config, brute):
    retriever = KnowledgeRetriever(db, base_config, embedding=FailEmbedder(), vec=brute)
    item_id = await _add(db, "Deploy guide", "docker compose deploy")

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
    await _embed_add(db, toy, "命名约定", "所有标识符使用蛇形命名法", vec=brute)

    # toy 词袋向量需较高 token 重叠才能过 0.6 门槛（真实 embedding 语义余弦天然更高）
    results = await retriever.search("标识符使用蛇形命名", "p1")
    assert len(results) == 1
    assert "蛇形命名法" in results[0].content


async def test_threshold_blocks_irrelevant(db, base_config, toy, brute):
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=brute)
    await _embed_add(db, toy, "Recipe", "banana bread walnuts sugar", vec=brute)

    # 零 token 重叠：cosine ≈ 0，BM25 无命中 → 不注入
    assert await retriever.search("kubernetes pod eviction", "p1") == []


async def test_dual_channel_ranks_first(db, base_config, toy, brute):
    """双通道命中的条目 RRF 融合后应排在纯单通道条目之前"""
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=brute)
    await _embed_add(db, toy, "Both", "docker deploy compose stack service", vec=brute)
    await _embed_add(db, toy, "FTS only", "docker deploy", vec=brute)  # 词项少，toy 向量 cosine 低

    results = await retriever.search("docker deploy compose", "p1")
    assert results
    assert results[0].title == "Both"


async def test_vector_project_isolation(db, base_config, toy, brute):
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=brute)
    await _embed_add(db, toy, "Secret", "internal deploy runbook", project="p1", vec=brute)

    assert await retriever.search("internal deploy runbook", "other") == []


async def test_vector_role_filter(db, base_config, toy, brute):
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=brute)
    await _embed_add(db, toy, "Dev doc", "registry mirror setup", roles=["developer"], vec=brute)

    assert await retriever.search("registry mirror", "p1", role="pm") == []
    assert len(await retriever.search("registry mirror", "p1", role="developer")) == 1


async def test_delete_removes_from_vector_index(db, base_config, toy, brute):
    retriever = KnowledgeRetriever(db, base_config, embedding=toy, vec=brute)
    item_id = await _embed_add(db, toy, "Guide", "docker compose deploy", vec=brute)
    assert await retriever.search("docker compose", "p1")

    conn = await db.connect()
    async with conn.execute("SELECT rowid FROM knowledge_items WHERE id = ?", (item_id,)) as cur:
        row = await cur.fetchone()
    rowid = int(row["rowid"])
    await conn.execute("DELETE FROM knowledge_items WHERE id = ?", (item_id,))
    await conn.commit()
    await brute.delete(rowid)
    retriever.invalidate_cache()
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
    await _embed_add(db, toy, "Deploy", "docker compose deploy stack", vec=sqlite_vec_backend)
    await _embed_add(db, toy, "Other", "banana bread recipe", vec=sqlite_vec_backend)

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
    rowids = []
    for title, content in docs:
        item_id = await _embed_add(db, toy, title, content, vec=brute)
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
