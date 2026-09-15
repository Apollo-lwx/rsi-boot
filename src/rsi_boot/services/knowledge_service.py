"""知识条目服务（add/list/search/delete）。

写入路径：MemoryStore 落 YAML（pending / official / review）；
检索走文件 RAG（_search_store）。审批通过后触发规则注入重写。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, List, Optional
from uuid import UUID

from ..core.models import KnowledgeItem
from ..injector.rule_injector import RuleInjector
from ..injector.slug import slugify
from ..memory.harvest import is_harvest_doc
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
    ) -> str:
        """写入知识条目。item.status 缺省 active；bootstrap 等批量来源传 pending_review
        走审批流（PRD v3.0 M2）——pending 条目不入检索/注入"""
        del api_key
        item_id = self._add_to_store(item)
        await self._notify_change(item.project_id or self.bound_project_id or "")
        return item_id

    def _add_to_store(self, item: KnowledgeItem) -> str:
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
            d for d in store.list_official()
            if d.status == "active" and d.type in _SHORT_KNOWLEDGE and not is_harvest_doc(d)
        ]
        docs.extend(
            d for d in store.list_pending()
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
        )

    def _read_reviewable(self, item_id: str) -> MemoryDoc | None:
        try:
            doc = self._store.read(item_id)
        except FileNotFoundError:
            return None
        if doc.status not in _REVIEWABLE_STATUSES:
            return None
        return doc

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
        返回 {processed, new_status}"""
        del project_id
        return await self._review_batch_store(
            approve, ids=ids, content_type=content_type,
            include_archived=include_archived, bootstrap_run_id=bootstrap_run_id,
            exclude_bootstrap=exclude_bootstrap, source_url=source_url,
        )

    async def delete(self, item_id: str, project_id: str) -> bool:
        try:
            self._store.move(item_id, _archive_dir(self._store))
        except FileNotFoundError:
            return False
        await self._notify_change(self._scope(project_id))
        return True
