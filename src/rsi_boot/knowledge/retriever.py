"""知识检索（P2.3：混合检索，§3.2）。

检索流程：BM25（FTS5）与向量（cosine）双通道各取 TopK → RRF 融合（k=60）→
注入门槛（原始分数：cosine ≥ 0.6 或 BM25 归一化 ≥ 0.6）→ TopN=5 → token 预算 2000 裁剪。

降级链（§2.3/§5.4）：sqlite-vec → brute（语义排序保留）→ embedding 失败时纯 FTS5。
_search_cjk 归位为「FTS5 兜底层的中文补丁」：仅在向量通道不可用且查询含 CJK 时启用
（向量可用时 embedding 天然处理中文语义，无需 bigram 补丁）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache

from ..core.models import KnowledgeItem
from ..data.sqlite import SQLiteClient
from ..data.vec import VectorBackend
from .embedding import EmbeddingService

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4
# CJK 查询走 bigram 重叠评分（FTS5 unicode61 不分词中文，整段会成为单一 token 无法匹配）
_CJK_RUN = re.compile(r"[一-鿿　-〿＀-￯]+")
_CJK_SCAN_LIMIT = 500

def _role_filter(prefix: str = "") -> str:
    """roles 为空数组（通用）或包含当前角色（§3.2 过滤与隔离）"""
    col = f"{prefix}roles" if prefix else "roles"
    return f"AND ({col} = '[]' OR EXISTS (SELECT 1 FROM json_each({col}) je WHERE je.value = ?))"


class KnowledgeRetriever:
    def __init__(
        self,
        db: SQLiteClient,
        config: dict[str, Any],
        embedding: Optional[EmbeddingService] = None,
        vec: Optional[VectorBackend] = None,
    ):
        self._db = db
        retrieval_cfg = config.get("retrieval", {})
        self._top_n = int(retrieval_cfg.get("top_n", 5))
        self._token_budget = int(retrieval_cfg.get("token_budget", 2000))
        self._threshold = float(retrieval_cfg.get("bm25_threshold", 0.6))
        self._cosine_threshold = float(retrieval_cfg.get("cosine_threshold", 0.6))
        self._channel_top_k = int(retrieval_cfg.get("channel_top_k", 20))
        self._rrf_k = float(retrieval_cfg.get("rrf_k", 60))
        self._embedding = embedding
        self._vec = vec
        # 检索结果缓存（§2.4，P4.4）：热点查询免回源；知识写入时 invalidate_cache 失效
        cache_ttl = float(retrieval_cfg.get("cache_ttl_s", 300))
        self._result_cache: Optional[TTLCache] = (
            TTLCache(maxsize=200, ttl=cache_ttl) if cache_ttl > 0 else None
        )

    async def search(
        self,
        query: str,
        project_id: str,
        role: Optional[str] = None,
        top_k: Optional[int] = None,
        api_key: Optional[str] = None,
        threshold: Optional[float] = None,
    ) -> List[KnowledgeItem]:
        """混合检索入口：结果缓存命中直接返回（§2.4），miss 走 _search_uncached。
        threshold 为召回臂注入的 BM25 门槛覆盖（Spec v3.0 §4.7），缺省用配置值"""
        if self._result_cache is None:
            return await self._search_uncached(query, project_id, role=role, top_k=top_k,
                                               api_key=api_key, threshold=threshold)
        key = (query, project_id, role or "", top_k or self._top_n, threshold or 0.0)
        cached = self._result_cache.get(key)
        if cached is not None:
            return list(cached)
        items = await self._search_uncached(query, project_id, role=role, top_k=top_k,
                                            api_key=api_key, threshold=threshold)
        self._result_cache[key] = list(items)
        return items

    def invalidate_cache(self) -> None:
        """知识写入/删除/审批后调用（KnowledgeService 写路径）"""
        if self._result_cache is not None:
            self._result_cache.clear()

    async def _search_uncached(
        self,
        query: str,
        project_id: str,
        role: Optional[str] = None,
        top_k: Optional[int] = None,
        api_key: Optional[str] = None,
        threshold: Optional[float] = None,
    ) -> List[KnowledgeItem]:
        """混合检索：双通道召回 → RRF 融合 → 原始分数门槛 → 预算裁剪（§3.2）"""
        limit = top_k or self._top_n
        bm25_threshold = threshold if threshold is not None else self._threshold
        has_cjk = bool(_CJK_RUN.search(query))

        # 向量通道（embedding 失败时静默降级为空，走纯 FTS5）
        vec_hits: List[Tuple[int, float]] = []
        if self._vec is not None and self._embedding is not None:
            qvec = await self._embedding.embed_one(query, api_key=api_key)
            if qvec is not None:
                try:
                    vec_hits = await self._vec.search(qvec, top_k=self._channel_top_k)
                except Exception as exc:
                    logger.warning("向量检索失败（降级纯 FTS5）: %s", exc)

        # FTS 通道（CJK 查询跳过：unicode61 不分词中文，匹配无意义）
        fts_hits: List[Tuple[int, float]] = []  # (rowid, 批内归一化 bm25)
        if not has_cjk:
            fts_hits = await self._fts_candidates(query, project_id, role)

        if not vec_hits and not fts_hits:
            if has_cjk:
                return await self._search_cjk(query, project_id, role, top_k)
            return []

        # 向量候选未带项目/角色过滤，统一在 SQL 层补齐（§3.2 过滤与隔离）
        candidate_rowids = {rid for rid, _ in vec_hits} | {rid for rid, _ in fts_hits}
        rows = await self._load_rows(candidate_rowids, project_id, role)

        # RRF 融合 + 各通道原始分数记录（门槛判断用原始分数，不用 RRF 分）
        rrf: Dict[int, float] = {}
        best_cosine: Dict[int, float] = {}
        best_bm25: Dict[int, float] = {}
        for rank, (rid, sim) in enumerate(vec_hits):
            if rid not in rows:
                continue
            rrf[rid] = rrf.get(rid, 0.0) + 1.0 / (self._rrf_k + rank + 1)
            best_cosine[rid] = max(best_cosine.get(rid, 0.0), sim)
        for rank, (rid, norm) in enumerate(fts_hits):
            if rid not in rows:
                continue
            rrf[rid] = rrf.get(rid, 0.0) + 1.0 / (self._rrf_k + rank + 1)
            best_bm25[rid] = max(best_bm25.get(rid, 0.0), norm)

        items: List[KnowledgeItem] = []
        budget_chars = self._token_budget * _CHARS_PER_TOKEN
        used_chars = 0
        for rid, _ in sorted(rrf.items(), key=lambda kv: kv[1], reverse=True):
            # 注入门槛：cosine ≥ 0.6 或 BM25 归一化 ≥ 门槛（「或」关系，§3.2；召回臂可覆盖 BM25 门槛）
            if best_cosine.get(rid, 0.0) < self._cosine_threshold and best_bm25.get(rid, 0.0) < bm25_threshold:
                continue
            row = rows[rid]
            if used_chars + len(row["content"]) > budget_chars:
                continue  # 超预算按分数从低到高裁剪
            used_chars += len(row["content"])
            items.append(self._row_to_item(row))
            if len(items) >= limit:
                break
        if not items and has_cjk:
            # CJK 查询无双通道高置信命中（向量不可用或低置信）→ bigram 补丁兜底
            return await self._search_cjk(query, project_id, role, top_k)
        return items

    async def _fts_candidates(
        self, query: str, project_id: str, role: Optional[str]
    ) -> List[Tuple[int, float]]:
        """FTS5 BM25 TopK + 批内 min-max 归一化（score / max）"""
        # FTS5 查询语法安全：逐词加引号，避免用户输入注入 FTS 操作符
        terms = [t for t in query.replace('"', " ").split() if t][:10]
        if not terms:
            return []
        fts_query = " OR ".join(f'"{t}"' for t in terms)

        conn = await self._db.connect()
        # bm25() 返回负值（越小越相关），取负转为「越大越相关」
        sql = f"""
            SELECT ki.rowid AS rid, -bm25(knowledge_fts) AS score
            FROM knowledge_fts
            JOIN knowledge_items ki ON ki.rowid = knowledge_fts.rowid
            WHERE knowledge_fts MATCH ?
              AND ki.project_id = ?
              AND ki.status = 'active'
              {_role_filter('ki.')}
            ORDER BY score DESC
            LIMIT ?
        """
        try:
            async with conn.execute(sql, (fts_query, project_id, role or "", self._channel_top_k)) as cur:
                rows = await cur.fetchall()
        except Exception as exc:
            logger.warning("FTS5 检索失败（向量通道仍可用）: %s", exc)
            return []
        if not rows:
            return []
        max_score = max(float(r["score"]) for r in rows) or 1.0
        return [(int(r["rid"]), float(r["score"]) / max_score) for r in rows]

    async def _load_rows(
        self, rowids: set[int], project_id: str, role: Optional[str]
    ) -> Dict[int, Any]:
        """按 rowid 加载候选行并补齐 project/role/status 过滤"""
        if not rowids:
            return {}
        conn = await self._db.connect()
        placeholders = ",".join("?" for _ in rowids)
        sql = (
            f"SELECT *, rowid AS rid FROM knowledge_items WHERE rowid IN ({placeholders}) "
            f"AND project_id = ? AND status = 'active' {_role_filter()}"
        )
        params = [*rowids, project_id, role or ""]
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return {int(r["rid"]): r for r in rows}

    async def _search_cjk(
        self,
        query: str,
        project_id: str,
        role: Optional[str],
        top_k: Optional[int],
    ) -> List[KnowledgeItem]:
        """含 CJK 的查询且无向量通道：扫描项目活跃条目，bigram/词项重叠评分"""
        terms = self._cjk_terms(query)
        if not terms:
            return []
        limit = top_k or self._top_n

        conn = await self._db.connect()
        sql = (
            "SELECT *, rowid AS rid FROM knowledge_items "
            f"WHERE project_id = ? AND status = 'active' {_role_filter()} "
            "LIMIT ?"
        )
        try:
            async with conn.execute(sql, (project_id, role or "", _CJK_SCAN_LIMIT)) as cur:
                rows = await cur.fetchall()
        except Exception as exc:
            logger.warning("CJK 检索失败（降级为无知识注入）: %s", exc)
            return []

        scored: list[tuple[float, int, Any]] = []
        for row in rows:
            text = f"{row['title']}\n{row['content']}".lower()
            hits = sum(1 for t in terms if t in text)
            if hits:
                scored.append((hits / len(terms), hits, row))
        scored.sort(key=lambda item: item[0], reverse=True)

        items: List[KnowledgeItem] = []
        budget_chars = self._token_budget * _CHARS_PER_TOKEN
        used_chars = 0
        # bigram 重叠率天然低于 BM25 归一化分：门槛降至 1/3，且至少命中 2 个词项防单词噪声
        cjk_threshold = self._threshold / 3
        for score, hits, row in scored:
            if hits < 2 or score < cjk_threshold:
                continue
            if used_chars + len(row["content"]) > budget_chars:
                continue
            used_chars += len(row["content"])
            items.append(self._row_to_item(row))
            if len(items) >= limit:
                break
        return items

    @staticmethod
    def _cjk_terms(query: str) -> List[str]:
        """CJK 连续段切 bigram（单字段取单字），非 CJK 段按空白取词，统一小写"""
        terms: List[str] = []
        for run in _CJK_RUN.finditer(query):
            text = run.group(0)
            if len(text) == 1:
                terms.append(text)
            else:
                terms.extend(text[i : i + 2] for i in range(len(text) - 1))
        for word in _CJK_RUN.sub(" ", query).split():
            terms.append(word.lower())
        return terms[:20]

    @staticmethod
    def _row_to_item(row: Any) -> KnowledgeItem:
        return KnowledgeItem(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            content=row["content"],
            content_type=row["content_type"] or "documentation",
            roles=json.loads(row["roles"] or "[]"),
            domain=row["domain"],
            tags=json.loads(row["tags"] or "[]"),
            source_url=row["source_url"],
            status=row["status"] or "active",
        )
