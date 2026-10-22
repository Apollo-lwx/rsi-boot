"""知识条目服务（add/list/search/delete）。

写入路径：MemoryStore 落 YAML（pending / official / review）；
检索走文件 RAG（_search_store）。审批通过后触发规则注入重写。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, List, Optional
from uuid import UUID

from ..core.models import KnowledgeItem
from ..injector.rule_injector import RuleInjector
from ..injector.slug import slugify
from ..memory.harvest import HARVEST_TITLES, is_harvest_doc
from ..memory.paths import official_dir, pending_dir, review_dir
from ..memory.store import MemoryStore, memory_filename
from ..memory.types import MEMORY_TYPES, MemoryDoc, type_from_legacy
from ..ux.lang import locale_lang
from ..ux.messages import t

logger = logging.getLogger(__name__)

_PENDING_TYPES = frozenset({"prohibition", "convention", "skill"})
_REVIEWABLE_STATUSES = frozenset({"pending_review", "archived"})
_SHORT_KNOWLEDGE = frozenset({"prohibition", "convention", "documentation"})


def _archive_dir(store: MemoryStore) -> Path:
    return store.rsi_dir / "memory" / "archive"


class KnowledgeService:
    def __init__(
        self,
        store: MemoryStore,
        bound_project_id: Optional[str] = None,
        project_root: Optional[Path] = None,
    ):
        self._store = store
        self.bound_project_id = bound_project_id
        self._project_root = Path(project_root) if project_root is not None else None
        self._injector: Optional[RuleInjector] = None
        if self._project_root is not None:
            self._injector = RuleInjector(store=store, project_root=self._project_root)
        # 记忆变更回调（Spec v3.0 §4.2：add/review/delete 后触发规则注入重写）
        self.on_change: Optional[Any] = None

    def _scope(self, project_id: Optional[str] = None) -> str:
        return project_id or self.bound_project_id or ""

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
        """状态变更（收敛/归档/复活）后触发注入重写"""
        await self._notify_change(project_id)

    async def add(
        self, item: KnowledgeItem, api_key: Optional[str] = None
    ) -> str | dict[str, Any]:
        """写入知识条目。校验失败返回 error dict，成功返回 id 字符串。"""
        del api_key
        item_id = self._add_to_store(item)
        if isinstance(item_id, dict):
            return item_id
        if "signal:distilled" in set(item.tags or []):
            return item_id
        await self._notify_change(item.project_id or self.bound_project_id or "")
        return item_id

    def _invalid(self, key: str) -> dict[str, Any]:
        return {
            "status": "error",
            "code": "invalid",
            "message": t(key, locale_lang()),
        }

    def _add_to_store(self, item: KnowledgeItem) -> str | dict[str, Any]:
        content = item.content or ""
        if len(content) > 1500:
            return self._invalid("KNOWLEDGE_TOO_LONG")
        # 元数据长度护栏（review S3）：条件字段/来源/域过短可控，避免绕过 content 上限
        for attr, cap in (
            ("source_url", 500),
            ("trigger_condition", 500),
            ("expiry_condition", 500),
            ("domain", 200),
        ):
            value = getattr(item, attr, None)
            if isinstance(value, str) and len(value) > cap:
                return self._invalid("KNOWLEDGE_TOO_LONG")
        if (item.title or "") in HARVEST_TITLES:
            return self._invalid("KNOWLEDGE_HARVEST_TITLE")
        tags = list(item.tags or [])
        if "signal:distilled" in tags and not any(
            str(tag).startswith("bootstrap_run_id:") for tag in tags
        ):
            return self._invalid("KNOWLEDGE_DISTILL_TAGS")
        store = self._store
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
            content=content,
            domain=item.domain,
            tags=list(item.tags),
            roles=list(item.roles),
            source=item.source_url or None,
            trigger_condition=item.trigger_condition,
            expiry_condition=item.expiry_condition,
            extra=extra,
        )
        src = item.source_url or ""
        lane_b = bool({"signal:conversation", "signal:rules"} & set(item.tags or []))
        lane_a_signal = src.startswith("signal:") and src not in (
            "signal:conversation", "signal:rules",
        )
        explicit_active = (item.status or "active") == "active"
        if "signal:distilled" in tags:
            dest = (
                pending_dir(store.rsi_dir, typ)
                if typ in _PENDING_TYPES
                else review_dir(store.rsi_dir, typ)
            ) / memory_filename(doc.title, item_id)
            written = store.write(doc, dest=dest)
            return written.id
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
        del project_id
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

    async def search(
        self,
        query: str,
        project_id: str,
        role: Optional[str] = None,
        top_k: int = 5,
        api_key: Optional[str] = None,
    ) -> List[KnowledgeItem]:
        del project_id, api_key
        return self._search_store(query, role=role, top_k=top_k)

    def _search_store(
        self, query: str, *, role: Optional[str] = None, top_k: int = 5
    ) -> List[KnowledgeItem]:
        from ..rag.retriever import retrieve

        store = self._store
        docs = [
            d for d in store.list_official(skip_harvest=True)
            if d.status == "active" and d.type in _SHORT_KNOWLEDGE and not is_harvest_doc(d)
        ]
        docs.extend(
            d for d in store.list_pending(skip_harvest=True)
            if d.type in _SHORT_KNOWLEDGE and not is_harvest_doc(d)
        )
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
            trigger_condition=doc.trigger_condition,
            expiry_condition=doc.expiry_condition,
        )

    def _read_reviewable(self, item_id: str) -> MemoryDoc | None:
        try:
            doc = self._store.read(item_id)
        except FileNotFoundError:
            return None
        if doc.status not in _REVIEWABLE_STATUSES:
            return None
        return doc

    def find_promotion_duplicates(
        self, item_id: str, *, threshold: float = 0.6
    ) -> list[dict[str, Any]]:
        """晋升前自检：待晋升条目与同类型已正式项的近重复候选（纯读，不改任何状态）。
        检测不可用（损坏 YAML / 未知类型等）时 fail-open 返回 []，绝不因此阻断主链路。"""
        from ..learning.dedup import find_near_duplicates

        try:
            doc = self._store.read(item_id)
            return find_near_duplicates(
                self._store,
                title=doc.title,
                content=doc.content,
                content_type=doc.type,
                exclude_id=doc.id,
                threshold=threshold,
            )
        except FileNotFoundError:
            return []
        except Exception:  # 检测异常不得阻断审批：记日志后放行（fail-open）
            logger.warning("find_promotion_duplicates failed for %s", item_id, exc_info=True)
            return []

    async def review_approve(self, item_id: str) -> dict[str, Any]:
        store = self._store
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
        src = store.rsi_dir / doc.path if doc.path else None
        written = store.write(doc, dest=dest)
        if src is not None and src.exists() and src.resolve() != dest.resolve():
            src.unlink()
        return written

    async def review(self, item_id: str, project_id: str, approve: bool) -> Optional[str]:
        """人工确认（§4.3）：pending_review 草稿 approve → official；
        reject → archive。返回新状态；条目不存在或非待审返回 None。
        archived（bootstrap 审批队列限量溢出）同样可审批恢复"""
        del project_id
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

    async def finalize_active(self, project_id: str, item_ids: List[str]) -> None:
        """冲突裁决后：失效检索缓存并重写注入。"""
        del item_ids
        await self._notify_change(self._scope(project_id))

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
        block_duplicates: bool = False,
        dedup_threshold: float = 0.6,
    ) -> dict[str, Any]:
        allowed = ("pending_review", "archived") if include_archived else ("pending_review",)
        id_set = set(ids) if ids else None
        marker = f"bootstrap_run_id:{bootstrap_run_id}" if bootstrap_run_id else None
        processed = 0
        deferred: List[dict[str, Any]] = []
        # review I4：批量晋升时把候选池一次性预载到内存，逐条走 pool_check/pool_add，
        # 避免 find_promotion_duplicates 内部 K×M 次 list_official+YAML 重解析；
        # 池本身的构建与逐条 tokenize 都丢进 asyncio.to_thread，不阻塞 MCP 事件循环。
        pool: dict[str, list[dict[str, Any]]] | None = None
        if approve and block_duplicates:
            from ..learning.dedup import build_promotion_pool

            pool = await asyncio.to_thread(build_promotion_pool, self._store)
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
                # 晋升前近重复自检（顺序审批天然覆盖同批互重：先晋升者已入正式库）
                if pool is not None:
                    from ..learning.dedup import pool_check

                    dups = await asyncio.to_thread(
                        pool_check, pool, doc, threshold=dedup_threshold,
                    )
                    if dups:
                        deferred.append({"id": doc.id, "possible_duplicates": dups})
                        continue
                await self.review_approve(doc.id)
                if pool is not None:
                    from ..learning.dedup import pool_add

                    await asyncio.to_thread(pool_add, pool, doc)
            else:
                await self.review_reject(doc.id)
            processed += 1
        result: dict[str, Any] = {
            "processed": processed,
            "new_status": "active" if approve else "archived",
        }
        if deferred:
            result["deferred_duplicates"] = deferred
        return result

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
        block_duplicates: bool = False,
        dedup_threshold: float = 0.6,
    ) -> dict[str, Any]:
        """批量审批（bootstrap 队列洪水场景）：默认处理全部 pending_review；
        ids 给定时只处理这些条目；content_type 过滤；include_archived 含限量溢出条目。
        bootstrap_run_id / exclude_bootstrap / source_url 隔离日常 auto-extract 与本轮抽取。
        block_duplicates=True 时晋升前拦下近重复（返回体附 deferred_duplicates）。
        返回 {processed, new_status[, deferred_duplicates]}"""
        del project_id
        return await self._review_batch_store(
            approve, ids=ids, content_type=content_type,
            include_archived=include_archived, bootstrap_run_id=bootstrap_run_id,
            exclude_bootstrap=exclude_bootstrap, source_url=source_url,
            block_duplicates=block_duplicates, dedup_threshold=dedup_threshold,
        )

    async def delete(self, item_id: str, project_id: str) -> bool:
        try:
            self._store.move(item_id, _archive_dir(self._store))
        except FileNotFoundError:
            return False
        await self._notify_change(self._scope(project_id))
        return True

    # ---- 原地更新 / 合并（对标 Qoder「update 优于新建、合并保留更完整者」）----
    # 更新只在写入侧做长度护栏（与 _add_to_store 同口径），读侧不拒绝旧数据。
    _UPDATE_FIELD_CAPS = {
        "domain": 200,
        "description": 500,
        "trigger_condition": 500,
        "expiry_condition": 500,
    }

    def _validate_update(self, doc: MemoryDoc, fields: dict[str, Any]) -> Optional[dict[str, Any]]:
        """返回 error dict 或 None。短知识正文沿用 1500 上限，案例类放宽到 20000。"""
        if "content" in fields:
            cap = 1500 if doc.type in _SHORT_KNOWLEDGE else 20000
            if len(str(fields["content"] or "")) > cap:
                return self._invalid("KNOWLEDGE_TOO_LONG")
        if "title" in fields and len(str(fields["title"] or "")) > 120:
            return self._invalid("KNOWLEDGE_TOO_LONG")
        for attr, cap in self._UPDATE_FIELD_CAPS.items():
            value = fields.get(attr)
            if isinstance(value, str) and len(value) > cap:
                return self._invalid("KNOWLEDGE_TOO_LONG")
        return None

    async def update(self, item_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """按 id 原地改字段：保留 id/created_at/所在目录，只覆盖传入的标量/列表字段。
        未知 id 返回 not_found；无有效字段返回 invalid。写回触发注入重写。"""
        allowed = (
            "title", "content", "tags", "roles", "domain",
            "description", "trigger_condition", "expiry_condition",
        )
        patch = {k: fields[k] for k in allowed if k in fields}
        if not patch:
            return {"status": "error", "code": "invalid",
                    "message": t("KNOWLEDGE_UPDATE_EMPTY", locale_lang())}
        doc = await asyncio.to_thread(self._safe_read, item_id)
        if doc is None:
            return {"status": "error", "code": "not_found",
                    "message": t("NOT_FOUND", locale_lang(), id=item_id)}
        if "content" in patch and not str(patch["content"] or "").strip():
            return {"status": "error", "code": "invalid",
                    "message": t("KNOWLEDGE_UPDATE_EMPTY", locale_lang())}
        err = self._validate_update(doc, patch)
        if err is not None:
            return err
        if "tags" in patch:
            patch["tags"] = [str(x) for x in (patch["tags"] or [])]
        if "roles" in patch:
            patch["roles"] = [str(x) for x in (patch["roles"] or [])]
        updated = await asyncio.to_thread(self._write_update, doc, patch)
        from ..learning.memory_health import close_awaiting_edits

        await asyncio.to_thread(close_awaiting_edits, self._store, updated.id)
        await self._notify_change(self.bound_project_id or "")
        return {
            "status": "success",
            "updated": sorted(patch),
            "data": {"id": updated.id, "title": updated.title, "type": updated.type,
                     "domain": updated.domain, "path": updated.path,
                     "updated_at": updated.updated_at},
        }

    def _safe_read(self, item_id: str) -> Optional[MemoryDoc]:
        try:
            return self._store.read(item_id)
        except (FileNotFoundError, ValueError):
            return None

    def _write_update(self, doc: MemoryDoc, patch: dict[str, Any]) -> MemoryDoc:
        """就地写回原路径（不重命名：记忆按文件内 id 认领，标题变了文件名 slug 滞后无碍）。"""
        dest = self._store.rsi_dir / doc.path if doc.path else official_dir(
            self._store.rsi_dir, doc.type) / memory_filename(doc.title, doc.id)
        return self._store.write(doc.model_copy(update=patch), dest=dest)

    async def merge(self, ids: List[str], keep_id: Optional[str] = None) -> dict[str, Any]:
        """合并近重复：保留更完整者（缺省取正文最长/最新），其余归档（非硬删，可恢复），
        keeper 记 merged_from 并各写一条 supersedes 边。不做正文自动拼接以免越界/串味。"""
        uniq = list(dict.fromkeys(str(i).strip().lower() for i in (ids or []) if str(i).strip()))
        if len(uniq) < 2:
            return {"status": "error", "code": "invalid",
                    "message": t("KNOWLEDGE_MERGE_NEED_TWO", locale_lang())}
        docs: dict[str, MemoryDoc] = {}
        for i in uniq:
            d = await asyncio.to_thread(self._safe_read, i)
            if d is not None:
                docs[i] = d
        if len(docs) < 2:
            return {"status": "error", "code": "not_found",
                    "message": t("KNOWLEDGE_MERGE_NEED_TWO", locale_lang())}
        if keep_id:
            keep_id = keep_id.strip().lower()
            if keep_id not in docs:
                return {"status": "error", "code": "not_found",
                        "message": t("NOT_FOUND", locale_lang(), id=keep_id)}
        else:
            keep_id = max(docs.values(), key=lambda d: (len(d.content or ""), d.updated_at or "")).id
        keeper = docs[keep_id]
        losers = [d for i, d in docs.items() if i != keep_id]
        result = await asyncio.to_thread(self._merge_store, keeper, losers)
        await self._notify_change(self.bound_project_id or "")
        return {
            "status": "success",
            "kept": keep_id,
            "archived": result["archived"],
            "edges": result["edges"],
        }

    def _merge_store(self, keeper: MemoryDoc, losers: List[MemoryDoc]) -> dict[str, Any]:
        from ..memory.graph import add_edge

        store = self._store
        merged = [dict(x) for x in (keeper.extra or {}).get("merged_from") or []]
        for loser in losers:
            store.move(loser.id, _archive_dir(store))
            add_edge(store.rsi_dir, keeper.id, loser.id, "supersedes")
            merged.append({"id": loser.id, "title": loser.title})
        extra = dict(keeper.extra or {})
        extra["merged_from"] = merged
        self._store.write(keeper.model_copy(update={"extra": extra}),
                          dest=store.rsi_dir / keeper.path)
        return {"archived": [l.id for l in losers], "edges": len(losers)}
