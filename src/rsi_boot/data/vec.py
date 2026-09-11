"""向量后端抽象（§2.3，P2.1）：VectorBackend 协议 + sqlite-vec / brute 双后端。

设计要点：
- embedding 的事实来源是 knowledge_items.embedding BLOB；vec0 仅为可重建加速索引
- 配置 vector.backend: auto | sqlite-vec | brute（默认 auto：探测扩展，缺失自动切 brute）
- 回退链：sqlite-vec → brute（语义排序保留）→ embedding 也失败时纯 FTS5（retriever 处理）
- vec0 表不在迁移中（扩展可能缺失），由后端在运行时创建；rowid 与 knowledge_items.rowid 对齐
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Protocol, Tuple, runtime_checkable

import numpy as np

from .sqlite import SQLiteClient

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1536  # text-embedding-3-small（§3.2：写入与查询同一模型，禁止混用）


@runtime_checkable
class VectorBackend(Protocol):
    """向量检索后端协议：upsert/delete/search。返回 (item_rowid, cosine_similarity)"""

    name: str

    async def upsert(self, item_rowid: int, vector: np.ndarray) -> None: ...
    async def delete(self, item_rowid: int) -> None: ...
    async def search(self, vector: np.ndarray, top_k: int = 20) -> List[Tuple[int, float]]: ...


class BruteBackend:
    """内置回退：从 knowledge_items.embedding BLOB 读全量，numpy 暴力余弦（≤1 万条 < 50ms）"""

    name = "brute"

    def __init__(self, db: SQLiteClient):
        self._db = db

    async def upsert(self, item_rowid: int, vector: np.ndarray) -> None:
        pass  # BLOB 已由 KnowledgeService 写入 knowledge_items，brute 无需额外索引

    async def delete(self, item_rowid: int) -> None:
        pass

    async def search(self, vector: np.ndarray, top_k: int = 20) -> List[Tuple[int, float]]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT rowid, embedding FROM knowledge_items "
            "WHERE embedding IS NOT NULL AND status = 'active'"
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return []

        matrix = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        query = vector.astype(np.float32)
        norms = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(query) or 1e-12)
        sims = (matrix @ query) / np.where(norms == 0, 1e-12, norms)
        top_idx = np.argsort(-sims)[:top_k]
        return [(int(rows[i]["rowid"]), float(sims[i])) for i in top_idx]


class SqliteVecBackend:
    """首选后端：vec0 虚拟表 cosine 搜索（rowid 对齐 knowledge_items.rowid，可重建）"""

    name = "sqlite-vec"

    def __init__(self, db: SQLiteClient):
        self._db = db
        self._ready = False

    async def _ensure_table(self) -> None:
        if self._ready:
            return
        conn = await self._db.connect()
        await conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vectors USING vec0(embedding FLOAT[{EMBEDDING_DIM}])"
        )
        await conn.commit()
        self._ready = True

    async def upsert(self, item_rowid: int, vector: np.ndarray) -> None:
        await self._ensure_table()
        conn = await self._db.connect()
        await conn.execute("DELETE FROM knowledge_vectors WHERE rowid = ?", (item_rowid,))
        await conn.execute(
            "INSERT INTO knowledge_vectors (rowid, embedding) VALUES (?, ?)",
            (item_rowid, vector.astype(np.float32).tobytes()),
        )
        await conn.commit()

    async def delete(self, item_rowid: int) -> None:
        await self._ensure_table()
        conn = await self._db.connect()
        await conn.execute("DELETE FROM knowledge_vectors WHERE rowid = ?", (item_rowid,))
        await conn.commit()

    async def search(self, vector: np.ndarray, top_k: int = 20) -> List[Tuple[int, float]]:
        await self._ensure_table()
        conn = await self._db.connect()
        # vec0 返回 cosine distance，转 similarity = 1 - distance
        async with conn.execute(
            "SELECT rowid, distance FROM knowledge_vectors WHERE embedding MATCH ? AND k = ?",
            (vector.astype(np.float32).tobytes(), top_k),
        ) as cur:
            rows = await cur.fetchall()
        return [(int(r["rowid"]), 1.0 - float(r["distance"])) for r in rows]


async def create_vector_backend(db: SQLiteClient, config: dict[str, Any]) -> Optional[VectorBackend]:
    """auto 探测：sqlite-vec 可用则首选，缺失自动切 brute 并记 warning（§2.3 回退链）"""
    backend_name = str(config.get("vector", {}).get("backend", "auto")).lower()
    if backend_name == "brute":
        return BruteBackend(db)

    if backend_name in ("auto", "sqlite-vec"):
        try:
            import sqlite_vec

            conn = await db.connect()
            await conn.enable_load_extension(True)
            try:
                # aiosqlite 无 run_in_executor：经 SQL load_extension 在 worker 线程加载
                path = sqlite_vec.loadable_path().replace("'", "''")
                await conn.execute(f"SELECT load_extension('{path}')")
            finally:
                await conn.enable_load_extension(False)
            backend = SqliteVecBackend(db)
            await backend._ensure_table()
            logger.info("向量后端: sqlite-vec")
            return backend
        except ImportError:
            if backend_name == "sqlite-vec":
                logger.warning("配置指定 sqlite-vec 但未安装（pip install rsi-boot[vec]），回退 brute")
            else:
                logger.info("sqlite-vec 未安装，向量后端: brute（numpy）")
            return BruteBackend(db)
        except Exception as exc:
            logger.warning("sqlite-vec 加载失败（%s），回退 brute", exc)
            return BruteBackend(db)

    logger.warning("未知 vector.backend=%s，向量检索禁用", backend_name)
    return None
