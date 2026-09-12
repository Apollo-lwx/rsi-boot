"""知识条目服务（add/list/search/delete）。

写入路径（§2.3）：knowledge_items 插入（含 embedding BLOB）→ 同步更新向量索引；
FTS 同步由 §2.2 触发器自动完成。嵌入生成失败时条目降级为仅 FTS 可检索
（记 warning，不阻塞写入）。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from ..core.masking import mask_text
from ..core.models import KnowledgeItem
from ..data.sqlite import SQLiteClient
from ..data.vec import VectorBackend
from ..knowledge.embedding import EmbeddingService, serialize_embedding
from ..knowledge.retriever import KnowledgeRetriever

logger = logging.getLogger(__name__)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeService:
    def __init__(
        self,
        db: SQLiteClient,
        retriever: KnowledgeRetriever,
        embedding: Optional[EmbeddingService] = None,
        vec: Optional[VectorBackend] = None,
        bound_project_id: Optional[str] = None,
    ):
        self._db = db
        self._retriever = retriever
        self._embedding = embedding
        self._vec = vec
        self.bound_project_id = bound_project_id
        # 记忆变更回调（Spec v3.0 §4.2：add/review/delete 后触发规则注入重写）
        self.on_change: Optional[Any] = None

    def _scope(self, project_id: Optional[str] = None) -> str:
        from ..project import bind_project_id

        return bind_project_id(self.bound_project_id, project_id)

    async def _notify_change(self, project_id: str) -> None:
        if self.on_change is None:
            return
        try:
            await self.on_change(project_id)
        except Exception:
            logger.exception("注入重写回调失败（下轮变更重试）")

    async def notify_changed(self, project_id: str) -> None:
        """裸 SQL 状态变更（收敛/归档/复活）后触发注入重写与缓存失效"""
        self._retriever.invalidate_cache()
        await self._notify_change(project_id)

    async def add(self, item: KnowledgeItem, api_key: Optional[str] = None) -> str:
        """写入知识条目。item.status 缺省 active；bootstrap 等批量来源传 pending_review
        走审批流（PRD v3.0 M2）——pending 条目不生成 embedding、不入检索/注入"""
        conn = await self._db.connect()
        item_id = uuid.uuid4().hex
        now = _utc_iso()
        title = mask_text(item.title)
        content = mask_text(item.content)
        status = item.status or "active"
        item.project_id = self._scope(item.project_id)

        # 嵌入生成（失败降级仅 FTS，不阻塞写入，§2.3）；pending_review 审批通过时才生成
        blob: Optional[bytes] = None
        vector = None
        if self._embedding is not None and status == "active":
            vector = await self._embedding.embed_one(f"{title}\n{content}", api_key=api_key)
            if vector is not None:
                blob = serialize_embedding(vector)

        # 注：vec0 仅为可重建加速索引（事实来源是 embedding BLOB，§2.3），
        # 索引更新失败不影响主写入，可随时从 BLOB 重建
        cur = await conn.execute(
            """
            INSERT INTO knowledge_items
                (id, project_id, title, content, content_type, roles, domain, tags,
                 source_url, status, embedding, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                item.project_id,
                title,
                content,
                item.content_type,
                json.dumps(item.roles, ensure_ascii=False),
                item.domain,
                json.dumps(item.tags, ensure_ascii=False),
                item.source_url,
                status,
                blob,
                now,
                now,
            ),
        )
        rowid = cur.lastrowid
        if vector is not None and self._vec is not None and rowid is not None:
            try:
                await self._vec.upsert(int(rowid), vector)
            except Exception as exc:
                logger.warning("向量索引更新失败（条目仍仅 FTS 可检索）: %s", exc)
        await conn.commit()
        self._retriever.invalidate_cache()  # §2.4：知识写入后检索结果缓存失效
        await self._notify_change(item.project_id)
        return item_id

    async def list(self, project_id: str, limit: int = 50) -> List[dict[str, Any]]:
        project_id = self._scope(project_id)
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT id, title, content_type, domain, tags, status, created_at "
            "FROM knowledge_items WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def search(
        self,
        query: str,
        project_id: str,
        role: Optional[str] = None,
        top_k: int = 5,
        api_key: Optional[str] = None,
    ) -> List[KnowledgeItem]:
        return await self._retriever.search(
            query, self._scope(project_id), role=role, top_k=top_k, api_key=api_key,
        )

    async def review(self, item_id: str, project_id: str, approve: bool) -> Optional[str]:
        """人工确认（§4.3）：pending_review 草稿 approve → active 并生成 embedding 入检索；
        reject → rejected（留存 30 天由每日任务清理）。返回新状态；条目不存在或非待审返回 None。
        archived（bootstrap 审批队列限量溢出）同样可审批恢复"""
        project_id = self._scope(project_id)
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT rowid, title, content, status FROM knowledge_items WHERE id = ? AND project_id = ?",
            (item_id, project_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["status"] not in ("pending_review", "archived"):
            return None

        now = _utc_iso()
        if not approve:
            await conn.execute(
                "UPDATE knowledge_items SET status = 'rejected', updated_at = ? WHERE id = ?",
                (now, item_id),
            )
            await conn.commit()
            self._retriever.invalidate_cache()
            return "rejected"  # 草稿从未入检索/注入，无需重写

        # 确认时才生成 embedding（§4.3）：此前 pending_review 不入向量/FTS 检索
        blob: Optional[bytes] = None
        vector = None
        if self._embedding is not None:
            vector = await self._embedding.embed_one(f"{row['title']}\n{row['content']}")
            if vector is not None:
                blob = serialize_embedding(vector)
        await conn.execute(
            "UPDATE knowledge_items SET status = 'active', embedding = ?, updated_at = ? WHERE id = ?",
            (blob, now, item_id),
        )
        if vector is not None and self._vec is not None:
            try:
                await self._vec.upsert(int(row["rowid"]), vector)
            except Exception as exc:
                logger.warning("向量索引更新失败（条目仍仅 FTS 可检索）: %s", exc)
        await conn.commit()
        self._retriever.invalidate_cache()
        await self._notify_change(project_id)  # 草稿转 active → 规则文件重写
        return "active"

    async def finalize_active(self, project_id: str, item_ids: List[str]) -> None:
        """冲突裁决后：给刚转 active 的条目补 embedding，失效检索缓存并重写注入。"""
        project_id = self._scope(project_id)
        conn = await self._db.connect()
        now = _utc_iso()
        for item_id in item_ids:
            async with conn.execute(
                "SELECT rowid, title, content, embedding FROM knowledge_items "
                "WHERE id = ? AND project_id = ? AND status = 'active'",
                (item_id, project_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None or row["embedding"] is not None:
                continue
            if self._embedding is None:
                continue
            vector = await self._embedding.embed_one(f"{row['title']}\n{row['content']}")
            if vector is None:
                continue
            await conn.execute(
                "UPDATE knowledge_items SET embedding = ?, updated_at = ? WHERE id = ?",
                (serialize_embedding(vector), now, item_id),
            )
            if self._vec is not None:
                try:
                    await self._vec.upsert(int(row["rowid"]), vector)
                except Exception as exc:
                    logger.warning("向量索引更新失败（条目仍仅 FTS 可检索）: %s", exc)
        await conn.commit()
        self._retriever.invalidate_cache()
        await self._notify_change(project_id)

    async def review_batch(
        self,
        project_id: str,
        approve: bool,
        *,
        ids: Optional[List[str]] = None,
        content_type: Optional[str] = None,
        include_archived: bool = False,
        bootstrap_run_id: Optional[str] = None,
        exclude_bootstrap: bool = False,
        source_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """批量审批（bootstrap 队列洪水场景）：默认处理全部 pending_review；
        ids 给定时只处理这些条目；content_type 过滤；include_archived 含限量溢出条目。
        bootstrap_run_id / exclude_bootstrap / source_url 隔离日常 auto-extract 与本轮抽取。
        单次提交 + 单次缓存失效 + 单次注入重写，返回 {processed, new_status}"""
        project_id = self._scope(project_id)
        conn = await self._db.connect()
        allowed = ("pending_review", "archived") if include_archived else ("pending_review",)
        clauses = [f"status IN ({','.join('?' for _ in allowed)})", "project_id = ?"]
        params: list[Any] = [*allowed, project_id]
        if include_archived:
            clauses.append("(status != 'archived' OR tags LIKE ?)")
            params.append("%bootstrap_run_id%")
        if ids:
            clauses.append(f"id IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        if content_type:
            clauses.append("content_type = ?")
            params.append(content_type)
        if bootstrap_run_id:
            clauses.append("tags LIKE ?")
            params.append(f"%bootstrap_run_id:{bootstrap_run_id}%")
            clauses.append("IFNULL(source_url, '') != 'auto-extract'")
        if exclude_bootstrap:
            clauses.append("tags NOT LIKE ?")
            params.append("%bootstrap_run_id%")
        if source_url is not None:
            clauses.append("source_url = ?")
            params.append(source_url)
        async with conn.execute(
            f"SELECT rowid, id, title, content FROM knowledge_items WHERE {' AND '.join(clauses)}",
            params,
        ) as cur:
            rows = await cur.fetchall()

        new_status = "active" if approve else "rejected"
        now = _utc_iso()
        for row in rows:
            blob: Optional[bytes] = None
            vector = None
            if approve and self._embedding is not None:
                vector = await self._embedding.embed_one(f"{row['title']}\n{row['content']}")
                if vector is not None:
                    blob = serialize_embedding(vector)
            await conn.execute(
                "UPDATE knowledge_items SET status = ?, embedding = ?, updated_at = ? WHERE id = ?",
                (new_status, blob, now, row["id"]),
            )
            if vector is not None and self._vec is not None:
                try:
                    await self._vec.upsert(int(row["rowid"]), vector)
                except Exception as exc:
                    logger.warning("向量索引更新失败（条目仍仅 FTS 可检索）: %s", exc)
        await conn.commit()
        if rows:
            self._retriever.invalidate_cache()
            if approve:
                await self._notify_change(project_id)  # 批量只重写一次规则文件
        return {"processed": len(rows), "new_status": new_status}

    async def delete(self, item_id: str, project_id: str) -> bool:
        project_id = self._scope(project_id)
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT rowid FROM knowledge_items WHERE id = ? AND project_id = ?", (item_id, project_id)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        await conn.execute(
            "DELETE FROM knowledge_items WHERE id = ? AND project_id = ?", (item_id, project_id)
        )
        if self._vec is not None:
            try:
                await self._vec.delete(int(row["rowid"]))
            except Exception as exc:
                logger.warning("向量索引删除失败（不影响主删除）: %s", exc)
        await conn.commit()
        self._retriever.invalidate_cache()
        await self._notify_change(project_id)
        return True
