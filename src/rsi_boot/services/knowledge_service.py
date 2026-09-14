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
from pathlib import Path
from typing import Any, List, Optional
from uuid import UUID

from ..core.masking import mask_text
from ..core.models import KnowledgeItem
from ..data.sqlite import SQLiteClient
from ..data.vec import VectorBackend
from ..injector.rule_injector import RuleInjector
from ..injector.slug import slugify
from ..knowledge.embedding import EmbeddingService, serialize_embedding
from ..knowledge.retriever import KnowledgeRetriever
from ..memory.paths import official_dir, pending_dir, review_dir
from ..memory.store import MemoryStore, memory_filename
from ..memory.types import MEMORY_TYPES, MemoryDoc, type_from_legacy
from ..ux.lang import locale_lang
from ..ux.messages import t

logger = logging.getLogger(__name__)

_PENDING_TYPES = frozenset({"prohibition", "convention", "skill"})
_REVIEWABLE_STATUSES = frozenset({"pending_review", "archived"})


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _archive_dir(store: MemoryStore) -> Path:
    return store.rsi_dir / "memory" / "archive"


class KnowledgeService:
    def __init__(
        self,
        db: Optional[SQLiteClient] = None,
        retriever: Optional[KnowledgeRetriever] = None,
        embedding: Optional[EmbeddingService] = None,
        vec: Optional[VectorBackend] = None,
        bound_project_id: Optional[str] = None,
        store: Optional[MemoryStore] = None,
        project_root: Optional[Path] = None,
    ):
        if store is None and (db is None or retriever is None):
            raise TypeError("db and retriever are required when store is omitted")
        self._store = store
        self._db = db
        self._retriever = retriever
        self._embedding = embedding
        self._vec = vec
        self.bound_project_id = bound_project_id
        self._project_root = Path(project_root) if project_root is not None else None
        self._injector: Optional[RuleInjector] = None
        if store is not None and self._project_root is not None:
            self._injector = RuleInjector(store=store, project_root=self._project_root)
        # 记忆变更回调（Spec v3.0 §4.2：add/review/delete 后触发规则注入重写）
        self.on_change: Optional[Any] = None

    def _scope(self, project_id: Optional[str] = None) -> str:
        if self._store is not None:
            return project_id or self.bound_project_id or ""
        from ..project import bind_project_id

        return bind_project_id(self.bound_project_id, project_id)

    def _invalidate(self) -> None:
        if self._retriever is not None:
            self._retriever.invalidate_cache()

    async def _notify_change(self, project_id: str) -> None:
        if self.on_change is not None:
            try:
                await self.on_change(project_id)
            except Exception:
                logger.exception("注入重写回调失败（下轮变更重试）")
            return
        if self._injector is None:
            return
        try:
            await self._injector.rewrite(project_id)
        except Exception:
            logger.exception("注入重写失败（下轮变更重试）")

    async def notify_changed(self, project_id: str) -> None:
        """裸 SQL 状态变更（收敛/归档/复活）后触发注入重写与缓存失效"""
        self._invalidate()
        await self._notify_change(project_id)

    async def add(
        self, item: KnowledgeItem, api_key: Optional[str] = None
    ) -> str:
        """写入知识条目。item.status 缺省 active；bootstrap 等批量来源传 pending_review
        走审批流（PRD v3.0 M2）——pending 条目不生成 embedding、不入检索/注入"""
        if self._store is not None:
            item_id = self._add_to_store(item)
            await self._notify_change(item.project_id or self.bound_project_id or "")
            return item_id
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
        self._invalidate()  # §2.4：知识写入后检索结果缓存失效
        await self._notify_change(item.project_id)
        return item_id

    def _add_to_store(self, item: KnowledgeItem) -> str:
        store = self._store
        assert store is not None
        typ, extra_update = type_from_legacy(item.content_type or "documentation")
        if typ not in MEMORY_TYPES:
            typ = "documentation"
        item_id = uuid.uuid4().hex
        extra = dict(extra_update)
        if item.source_url:
            extra["source_url"] = item.source_url
        doc = MemoryDoc(
            id=item_id,
            type=typ,
            title=item.title[:120],
            content=item.content[:20000],
            domain=item.domain,
            tags=list(item.tags),
            roles=list(item.roles),
            source=item.source_url or None,
            extra=extra,
        )
        src = item.source_url or ""
        lane_b = bool({"signal:conversation", "signal:rules"} & set(item.tags or []))
        lane_a_signal = src.startswith("signal:") and src not in (
            "signal:conversation", "signal:rules",
        )
        explicit_active = (item.status or "active") == "active"
        if typ in _PENDING_TYPES:
            # MCP add: default active + empty source → pending. Bootstrap lane-A
            # (signal:config/code/git) and explicit active with a source → official.
            if explicit_active and (lane_a_signal or (src and not lane_b)):
                dest = official_dir(store.rsi_dir, typ) / memory_filename(doc.title, item_id)
            else:
                dest = pending_dir(store.rsi_dir, typ) / memory_filename(doc.title, item_id)
        elif (item.status or "active") == "pending_review":
            dest = review_dir(store.rsi_dir, typ) / memory_filename(doc.title, item_id)
        else:
            dest = official_dir(store.rsi_dir, typ) / memory_filename(doc.title, item_id)
        written = store.write(doc, dest=dest)
        return written.id

    async def list(self, project_id: str, limit: int = 50) -> List[dict[str, Any]]:
        if self._store is not None:
            docs = list(self._store.list_official()) + list(self._store.list_pending())
            docs.sort(key=lambda d: d.created_at or "", reverse=True)
            return [
                {
                    "id": d.id,
                    "title": d.title,
                    "content_type": d.type,
                    "domain": d.domain,
                    "tags": d.tags,
                    "status": d.status,
                    "created_at": d.created_at,
                }
                for d in docs[:limit]
            ]
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
        if self._store is not None:
            return self._search_store(query, role=role, top_k=top_k)
        return await self._retriever.search(
            query, self._scope(project_id), role=role, top_k=top_k, api_key=api_key,
        )

    def _search_store(
        self, query: str, *, role: Optional[str] = None, top_k: int = 5
    ) -> List[KnowledgeItem]:
        from ..rag.retriever import retrieve

        store = self._store
        assert store is not None
        docs = [d for d in store.list_official() if d.status == "active"]
        if role:
            docs = [d for d in docs if not d.roles or role in d.roles]
        by_id = {d.id: d for d in docs}
        hits = retrieve(docs, query, role=role, top_n=top_k, rsi_dir=store.rsi_dir)
        items: List[KnowledgeItem] = []
        for doc_id, _score in hits:
            doc = by_id.get(doc_id)
            if doc is None:
                continue
            items.append(self._doc_to_item(doc))
        return items

    def _doc_to_item(self, doc: MemoryDoc) -> KnowledgeItem:
        return KnowledgeItem(
            id=UUID(hex=doc.id),
            project_id=self.bound_project_id or "",
            title=doc.title,
            content=doc.content,
            content_type=doc.type,
            roles=list(doc.roles),
            domain=doc.domain,
            tags=list(doc.tags),
            status=doc.status,
        )

    def _read_reviewable(self, item_id: str) -> MemoryDoc | None:
        store = self._store
        assert store is not None
        try:
            doc = store.read(item_id)
        except FileNotFoundError:
            return None
        if doc.status not in _REVIEWABLE_STATUSES:
            return None
        return doc

    async def review_approve(self, item_id: str) -> dict[str, Any]:
        store = self._store
        if store is None:
            raise RuntimeError("review_approve requires store")
        doc = self._read_reviewable(item_id)
        if doc is None:
            raise FileNotFoundError(item_id)
        old_path = doc.path
        if doc.type == "skill":
            name = str(doc.payload.get("name") or slugify(doc.title))
            dest = official_dir(store.rsi_dir, "skill") / name / "skill.yaml"
            written = self._relocate(doc, dest)
        else:
            written = store.move(item_id, official_dir(store.rsi_dir, doc.type))
        await self._notify_change(self.bound_project_id or "")
        return {
            "moved": [{"from": old_path, "to": written.path}],
            "message": t("REVIEW_APPROVED", locale_lang(), n=1),
        }

    async def review_reject(self, item_id: str) -> dict[str, Any]:
        store = self._store
        if store is None:
            raise RuntimeError("review_reject requires store")
        doc = self._read_reviewable(item_id)
        if doc is None:
            raise FileNotFoundError(item_id)
        old_path = doc.path
        extra = dict(doc.extra)
        extra["review"] = "rejected"
        updated = doc.model_copy(update={"extra": extra})
        dest = _archive_dir(store) / memory_filename(doc.title, doc.id)
        written = self._relocate(updated, dest)
        await self._notify_change(self.bound_project_id or "")
        return {
            "moved": [{"from": old_path, "to": written.path}],
            "message": t("REVIEW_REJECTED", locale_lang(), n=1),
        }

    def _relocate(self, doc: MemoryDoc, dest: Path) -> MemoryDoc:
        store = self._store
        assert store is not None
        src = store.rsi_dir / doc.path if doc.path else None
        written = store.write(doc, dest=dest)
        if src is not None and src.exists() and src.resolve() != dest.resolve():
            src.unlink()
        return written

    async def review(self, item_id: str, project_id: str, approve: bool) -> Optional[str]:
        """人工确认（§4.3）：pending_review 草稿 approve → active 并生成 embedding 入检索；
        reject → rejected（留存 30 天由每日任务清理）。返回新状态；条目不存在或非待审返回 None。
        archived（bootstrap 审批队列限量溢出）同样可审批恢复"""
        if self._store is not None:
            if self._read_reviewable(item_id) is None:
                return None
            try:
                if approve:
                    await self.review_approve(item_id)
                    return "active"
                await self.review_reject(item_id)
                return "archived"
            except FileNotFoundError:
                return None
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
            self._invalidate()
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
        self._invalidate()
        await self._notify_change(project_id)  # 草稿转 active → 规则文件重写
        return "active"

    async def finalize_active(self, project_id: str, item_ids: List[str]) -> None:
        """冲突裁决后：给刚转 active 的条目补 embedding，失效检索缓存并重写注入。"""
        project_id = self._scope(project_id)
        if self._store is not None:
            del item_ids
            self._invalidate()
            await self._notify_change(project_id)
            return
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
        self._invalidate()
        await self._notify_change(project_id)

    def _doc_source_url(self, doc: MemoryDoc) -> str:
        return str(doc.extra.get("source_url") or doc.source or "")

    async def _review_batch_store(
        self,
        approve: bool,
        *,
        ids: Optional[List[str]] = None,
        content_type: Optional[str] = None,
        include_archived: bool = False,
        bootstrap_run_id: Optional[str] = None,
        exclude_bootstrap: bool = False,
        source_url: Optional[str] = None,
    ) -> dict[str, Any]:
        allowed = ("pending_review", "archived") if include_archived else ("pending_review",)
        id_set = set(ids) if ids else None
        marker = f"bootstrap_run_id:{bootstrap_run_id}" if bootstrap_run_id else None
        processed = 0
        for doc in list(self._store.list_all()):
            if doc.status not in allowed:
                continue
            if include_archived and doc.status == "archived":
                if not any(str(t).startswith("bootstrap_run_id") for t in (doc.tags or [])):
                    continue
            if id_set is not None and doc.id not in id_set:
                continue
            if content_type and doc.type != content_type:
                continue
            tags = list(doc.tags or [])
            if marker and marker not in tags:
                continue
            if bootstrap_run_id and self._doc_source_url(doc) == "auto-extract":
                continue
            if exclude_bootstrap and any(str(t).startswith("bootstrap_run_id") for t in tags):
                continue
            if source_url is not None and self._doc_source_url(doc) != source_url:
                continue
            if approve:
                await self.review_approve(doc.id)
            else:
                await self.review_reject(doc.id)
            processed += 1
        return {"processed": processed, "new_status": "active" if approve else "archived"}

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
        if self._store is not None:
            return await self._review_batch_store(
                approve, ids=ids, content_type=content_type,
                include_archived=include_archived, bootstrap_run_id=bootstrap_run_id,
                exclude_bootstrap=exclude_bootstrap, source_url=source_url,
            )
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
            self._invalidate()
            if approve:
                await self._notify_change(project_id)  # 批量只重写一次规则文件
        return {"processed": len(rows), "new_status": new_status}

    async def delete(self, item_id: str, project_id: str) -> bool:
        if self._store is not None:
            try:
                self._store.move(item_id, _archive_dir(self._store))
            except FileNotFoundError:
                return False
            await self._notify_change(self._scope(project_id))
            return True
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
        self._invalidate()
        await self._notify_change(project_id)
        return True
