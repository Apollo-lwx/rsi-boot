"""记忆召回服务（Spec v3.0 §4.3）：rsi_recall 的内核。

召回排序：禁止项命中一律置顶（不受分数门槛限制）→ 其余走检索管线。
每次召回落一行 events.jsonl（kind='recall'），feedback_token
照常签发——反馈闭环据此驱动召回臂学习与采纳率统计。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.models import RSIRequest, generate_feedback_token
from ..memory.harvest import is_harvest_doc
from ..memory.store import MemoryStore
from ..memory.types import MemoryDoc
from ..strategy.recall import RecallArmSelector
from .decision_queue import DecisionQueue, collect_decision_cards
from .profile_service import ProfileService

logger = logging.getLogger(__name__)

_RECALL_SCAN_LIMIT = 200  # 禁止项匹配的项目内扫描上限
_STORE_DEFAULT_ARM = "recall-balanced"
_STORE_DEFAULT_TOP_N = 5
_ITEM_TYPES = frozenset({"convention", "documentation"})
_SEARCH_TYPES = frozenset({
    "prohibition", "convention", "documentation",
    "gene_case", "teaching_case", "episode",
})
_CASE_ORDER = ("prohibition", "convention", "documentation", "teaching_case", "gene_case", "episode")


class RecallService:
    def __init__(
        self,
        store: MemoryStore,
        feedback_secret: str = "",
        profiles: Optional[ProfileService] = None,
        bound_project_id: Optional[str] = None,
        decisions: Optional[DecisionQueue] = None,
        project_root: Optional[Path] = None,
        arms: Optional[RecallArmSelector] = None,
    ):
        self._store = store
        self._arms = arms
        self._secret = feedback_secret
        self._profiles = profiles
        self.bound_project_id = bound_project_id
        self._decisions = decisions if decisions is not None else DecisionQueue()
        self._project_root = project_root
        self._bg_tasks: set[asyncio.Task] = set()  # 画像增量后台任务，close 前 drain

    async def recall(
        self,
        task: str,
        project_id: str,
        user_id: str = "local",
        role: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await self._recall_from_store(task, project_id, user_id, role, top_k)

    async def _recall_from_store(
        self,
        task: str,
        project_id: str,
        user_id: str,
        role: Optional[str],
        top_k: Optional[int],
    ) -> Dict[str, Any]:
        from ..memory.logstore import append_event
        from ..project import bind_project_id
        from ..rag.index import build_index, search
        from ..rag.query import expand_query

        store = self._store
        project_id = bind_project_id(self.bound_project_id, project_id)
        started = time.monotonic()
        limit = min(top_k, 10) if top_k else _STORE_DEFAULT_TOP_N

        docs = self._store_search_docs(store, role)
        by_id = {doc.id: doc for doc in docs}
        expanded, intent, _confidence = expand_query(
            task, role=role, rsi_dir=store.rsi_dir,
        )
        index = build_index(docs)

        from ..rag.index import best_heading
        from ..ux.lang import detect_lang
        from ..ux.messages import t

        prohibitions = [
            by_id[doc_id]
            for doc_id, _score in search(index, expanded, types={"prohibition"}, top_n=_RECALL_SCAN_LIMIT)
            if doc_id in by_id
        ]
        items = [
            by_id[doc_id]
            for doc_id, _score in search(index, expanded, types=_ITEM_TYPES, top_n=limit)
            if doc_id in by_id
        ]
        teaching = [
            by_id[doc_id]
            for doc_id, _score in search(index, expanded, types={"teaching_case"}, top_n=limit)
            if doc_id in by_id
        ]
        teach_ids = {doc.id for doc in teaching}
        genes = [
            by_id[doc_id]
            for doc_id, _score in search(index, expanded, types={"gene_case"}, top_n=limit)
            if doc_id in by_id and doc_id not in teach_ids
        ]
        yaml_episodes = [
            by_id[doc_id]
            for doc_id, _score in search(index, expanded, types={"episode"}, top_n=limit)
            if doc_id in by_id
        ]
        event_episodes = self._episodes_from_events(store, task, expanded)
        episodes = self._merge_episodes(yaml_episodes, event_episodes)
        skills = self._store_skill_catalog(store)
        hits = prohibitions + items + genes + teaching
        latency_ms = int((time.monotonic() - started) * 1000)

        request = RSIRequest(user_id=user_id, project_id=project_id, role=role, raw_input=task)
        token = generate_feedback_token(request.request_id, user_id, self._secret)
        retrieved = [doc.id for doc in hits]
        for row in episodes:
            eid = row.get("id")
            if isinstance(eid, str) and eid not in retrieved:
                retrieved.append(eid)
        event: Dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": "recall",
            "task": task,
            "retrieved": retrieved,
            "arm": _STORE_DEFAULT_ARM,
            "token": token,
            "request_id": str(request.request_id),
            "user_id": user_id,
            "project_id": project_id,
            "intent": intent,
            "latency_ms": latency_ms,
        }
        if hits:
            event["excerpt"] = "\n".join(doc.title for doc in hits)
        append_event(store.rsi_dir, event)

        cards = await collect_decision_cards(
            store, project_id, project_root=self._project_root,
        )
        picked = self._decisions.pick(cards)
        decisions: list[dict[str, Any]] = []
        if picked is not None:
            picked.more_waiting = self._decisions.remaining_after(cards, picked)
            self._decisions.mark_presented(picked.id)
            decisions = [picked.asdict()]

        return {
            "prohibitions": [self._prohibition_payload(doc) for doc in prohibitions],
            "items": [
                self._item_payload(doc, heading=best_heading(index, doc.id, expanded))
                for doc in items
            ],
            "gene_cases": [
                self._case_payload(doc, heading=best_heading(index, doc.id, expanded))
                for doc in genes
            ],
            "teaching_cases": [
                self._case_payload(doc, heading=best_heading(index, doc.id, expanded))
                for doc in teaching
            ],
            "episodes": episodes,
            "skills": skills,
            "hint": t("HINT_TEACH", detect_lang(task)),
            "feedback_token": token,
            "recall_arm": _STORE_DEFAULT_ARM,
            "decisions": decisions,
        }

    def _store_search_docs(self, store: MemoryStore, role: Optional[str]) -> List[MemoryDoc]:
        docs: List[MemoryDoc] = []
        teaching_ids: set[str] = set()
        for typ in _CASE_ORDER:
            for doc in store.list_official(typ):
                if doc.status != "active" or doc.type not in _SEARCH_TYPES:
                    continue
                if doc.type in _ITEM_TYPES or doc.type == "prohibition":
                    if is_harvest_doc(doc):
                        continue
                if doc.roles and role and role not in doc.roles:
                    continue
                if typ == "teaching_case":
                    teaching_ids.add(doc.id)
                if typ == "gene_case" and doc.id in teaching_ids:
                    continue
                docs.append(doc)
        return docs

    def _store_skill_catalog(self, store: MemoryStore) -> List[Dict[str, Any]]:
        catalog: List[Dict[str, Any]] = []
        for doc in store.list_official("skill"):
            if doc.status != "active":
                continue
            catalog.append({
                "name": str(doc.payload.get("name") or doc.title),
                "description": doc.description or "",
                "path": doc.path or "",
            })
        return catalog

    @staticmethod
    def _prohibition_payload(doc: MemoryDoc) -> Dict[str, Any]:
        return {
            "id": doc.id,
            "title": doc.title,
            "content": doc.content,
            "domain": doc.domain,
            "source_path": doc.path,
        }

    @staticmethod
    def _item_payload(doc: MemoryDoc, heading: str = "") -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": doc.id,
            "title": doc.title,
            "content": doc.content,
            "content_type": doc.type,
            "source_path": doc.path,
            "heading": heading,
        }
        extra = doc.extra or {}
        nested = doc.payload or {}
        for key in ("weight", "conflict_open"):
            if key in extra:
                payload[key] = extra[key]
            elif key in nested:
                payload[key] = nested[key]
        return payload

    @staticmethod
    def _case_payload(doc: MemoryDoc, heading: str = "") -> Dict[str, Any]:
        return {
            "id": doc.id,
            "title": doc.title,
            "content": doc.content,
            "source_path": doc.path,
            "heading": heading,
        }

    def _episodes_from_events(
        self, store: MemoryStore, task: str, expanded: str,
    ) -> List[Dict[str, Any]]:
        from ..memory.logstore import iter_events
        from ..rag.index import tokenize
        from ..rag.query import expand_query

        def _fp_terms(text: str) -> set[str]:
            ev_exp, ev_intent, _ = expand_query(text, rsi_dir=store.rsi_dir)
            return set(tokenize(ev_exp)) - set(tokenize(ev_intent))

        q_terms = _fp_terms(task)
        if not q_terms:
            q_terms = set(tokenize(expanded))
        events = list(iter_events(store.rsi_dir))
        recalls = [
            ev for ev in events
            if ev.get("kind") == "recall" and ev.get("task") and ev.get("id")
        ]
        feedbacks = [ev for ev in events if ev.get("kind") == "feedback"]
        matched: List[Dict[str, Any]] = []
        for ev in recalls:
            ev_task = str(ev["task"])
            if _fp_terms(ev_task) & q_terms:
                matched.append(ev)
        if not matched:
            return []

        groups: Dict[str, List[Dict[str, Any]]] = {}
        for ev in matched:
            key = " ".join(sorted(_fp_terms(str(ev["task"]))))
            groups.setdefault(key, []).append(ev)

        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for key, group in groups.items():
            tokens = {ev.get("token") for ev in group if ev.get("token")}
            fbs = [fb for fb in feedbacks if fb.get("token") in tokens]
            should_write = (
                len(fbs) >= 2
                or any(int(fb.get("rating") or 0) >= 4 for fb in fbs)
                or any(fb.get("action") == "rejected" and fb.get("comment") for fb in fbs)
            )
            representative = group[0]
            eid = str(representative["id"])
            path = self._materialize_episode(store, representative, key) if should_write else ""
            if eid in seen:
                continue
            seen.add(eid)
            out.append({
                "id": eid,
                "title": str(representative.get("task") or "")[:120],
                "task": representative.get("task"),
                "content": representative.get("excerpt") or representative.get("task") or "",
                "source_path": path,
            })
        return out

    def _materialize_episode(
        self, store: MemoryStore, ev: Dict[str, Any], fingerprint: str,
    ) -> str:
        from ..memory.paths import official_dir
        from ..memory.store import memory_filename

        eid = str(ev["id"])
        try:
            existing = store.read(eid)
            if existing.type == "episode" and existing.path:
                return existing.path
        except FileNotFoundError:
            pass
        title = str(ev.get("task") or "episode")[:120]
        dest = official_dir(store.rsi_dir, "episode") / memory_filename(title, eid)
        written = store.write(
            MemoryDoc(
                id=eid,
                type="episode",
                title=title,
                content=str(ev.get("excerpt") or ev.get("task") or "")[:20000],
                payload={"fingerprint": fingerprint},
            ),
            dest=dest,
        )
        return written.path or ""

    @staticmethod
    def _merge_episodes(
        yaml_docs: List[MemoryDoc], event_rows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        by_id: Dict[str, Dict[str, Any]] = {}
        for doc in yaml_docs:
            by_id[doc.id] = {
                "id": doc.id,
                "title": doc.title,
                "content": doc.content,
                "source_path": doc.path,
                "task": doc.title,
            }
        for row in event_rows:
            eid = str(row.get("id") or "")
            if not eid:
                continue
            if eid in by_id:
                if row.get("source_path") and not by_id[eid].get("source_path"):
                    by_id[eid]["source_path"] = row["source_path"]
            else:
                by_id[eid] = row
        return list(by_id.values())

    async def drain(self, timeout: float = 2.0) -> None:
        """等待后台画像任务完成（Runtime.close 在关库前调用）"""
        pending = [t for t in self._bg_tasks if not t.done()]
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout)
            except asyncio.TimeoutError:
                logger.warning("画像后台任务 drain 超时（%d 个未完成）", len(pending))
