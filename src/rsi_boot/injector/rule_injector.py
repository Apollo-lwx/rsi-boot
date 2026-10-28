"""规则文件注入编排（Spec v3.0 §4.2）：记忆变更 → 全量幂等重写 → 台账同步。

触发点：知识 add/review/delete、提案晋升/回滚（经 KnowledgeService.on_change 与
ProposalEngine 回调注入）。同一项目的重写经 asyncio.Lock 串行化，多变更事件合并。
注入内容过三道闸：§8.1 脱敏（写入库前已执行）+ 审批闸门（仅 active 注入）+
黑名单过滤（本类执行）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .blocklist import is_blocked
from ..memory.tree import TREE_TYPES
from .targets import (
    MAX_FILE_CHARS,
    MAX_TOTAL_CHARS,
    AgentsMdTarget,
    Artifact,
    CursorRuleTarget,
    MemoryBundle,
    MemoryRow,
    RuleTarget,
    _mdc,
)

if TYPE_CHECKING:
    from ..memory.store import MemoryStore
    from ..memory.types import MemoryDoc

logger = logging.getLogger(__name__)

_ADOPTION_ACTIONS = frozenset({"accepted", "applied", "copied", "referenced"})


def _prohibition_mdc_len(title: str, content: str) -> int:
    return len(_mdc(title, "", True, content[:MAX_FILE_CHARS]))


class RuleInjector:
    def __init__(
        self,
        store: MemoryStore,
        project_root: Optional[Path] = None,
    ):
        if store is None:
            raise TypeError("store is required")
        self._store = store
        self._targets: List[RuleTarget] = []
        if project_root is not None:
            self._targets = [CursorRuleTarget(project_root), AgentsMdTarget(project_root)]
        self._locks: Dict[str, asyncio.Lock] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._targets)

    async def rewrite(self, project_id: str = "") -> Dict[str, int]:
        """全量重写该项目全部载体的规则产物并同步台账。无载体（无项目根）时静默跳过"""
        if not self._targets:
            return {"written": 0, "removed": 0}
        lock_key = project_id or str(self._store.rsi_dir)
        lock = self._locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            bundle = self._load_bundle()
            written: List[Artifact] = []
            for target in self._targets:
                try:
                    written.extend(target.write(bundle))
                except OSError as exc:
                    # §8 韧性：规则文件不可写不阻塞主链路，下轮事件重试
                    logger.error("载体 %s 写入失败（下轮变更重试）: %s", target.name, exc)
            removed = 0
            logger.info("规则注入完成（项目 %s）：%d 个文件，移除 %d 个", project_id, len(written), removed)
            return {"written": len(written), "removed": removed}

    def _load_bundle(self) -> MemoryBundle:
        """正式 prohibitions/ + skills/*/skill.yaml 的 name/description；不注入 convention。"""
        store = self._store
        docs = [d for d in store.list_official("prohibition") if d.status == "active"]
        kept = self._trim_prohibitions(docs)
        bundle = MemoryBundle(inject_conventions=False)
        for doc in kept:
            if is_blocked(f"{doc.title}\n{doc.content}"):
                logger.warning("记忆 %s 命中注入黑名单，跳过注入", doc.id[:8])
                continue
            bundle.prohibitions.append(
                MemoryRow(
                    id=doc.id, title=doc.title, content=doc.content,
                    content_type="prohibition", domain=doc.domain,
                )
            )
        for doc in store.list_official("skill"):
            rel = (doc.path or "").replace("\\", "/")
            if not rel.endswith("skill.yaml"):
                continue
            name = str(doc.payload.get("name") or doc.title)
            desc = doc.description or ""
            if is_blocked(f"{name}\n{desc}"):
                logger.warning("技能 %s 命中注入黑名单，跳过注入", doc.id[:8])
                continue
            bundle.skills.append(
                MemoryRow(
                    id=doc.id, title=name, content=desc,
                    content_type="skill", domain=rel,
                )
            )
        bundle.catalog, bundle.tree_lines = self._load_catalog_and_tree()
        return bundle

    #: 目录覆盖的类型：正文不进宿主上下文，但标题+简述被动可见（详情走 rsi_recall）
    _CATALOG_TYPES = TREE_TYPES

    def _load_catalog_and_tree(
        self, cap: int = 40
    ) -> tuple[List[MemoryRow], List[str]]:
        """一次扫描同时产出：扁平记忆目录（标题+简述，封顶 cap 条）与层级知识树
        （按 domain 分段的板块计数）。简述只取显式 description（正文一律不进宿主上下文，
        详情走 rsi_recall / rsi_explore）；命中注入黑名单的条目两处均跳过。

        catalog 排序（review S4）：早期实现按 TREE_TYPES 顺序 + 文件名字母序取 cap，
        结果 documentation 若条目多就会占满槽位，teaching/gene/episode 永远进不来。
        改成"每类保底 1 条 + 剩余按 updated_at（缺则 created_at）全局倒序"，
        兼顾新鲜度与板块可见性；tree 用全量 docs。
        """
        store = self._store
        all_docs: List[Any] = []
        per_type: dict[str, list] = {typ: [] for typ in self._CATALOG_TYPES}
        for typ in self._CATALOG_TYPES:
            for doc in store.list_official(typ, skip_harvest=True):
                if doc.status != "active":
                    continue
                summary = " ".join((doc.description or "").split())[:80]
                if is_blocked(f"{doc.title}\n{summary}"):
                    continue
                all_docs.append(doc)
                per_type[typ].append(doc)

        def sort_key(doc) -> tuple[str, str]:
            ts = str(getattr(doc, "updated_at", "") or getattr(doc, "created_at", "") or "")
            return (ts, str(getattr(doc, "id", "") or ""))

        seed: List[Any] = []
        chosen_ids: set = set()
        for typ in self._CATALOG_TYPES:
            bucket = per_type[typ]
            if not bucket:
                continue
            pick = max(bucket, key=sort_key)
            seed.append(pick)
            chosen_ids.add(pick.id)
        rest = [d for d in all_docs if d.id not in chosen_ids]
        ordered = seed + sorted(rest, key=sort_key, reverse=True)

        rows: List[MemoryRow] = []
        for doc in ordered[:cap]:
            summary = " ".join((doc.description or "").split())[:80]
            rows.append(
                MemoryRow(
                    id=doc.id, title=doc.title, content=summary,
                    content_type=str(doc.type or ""), domain=doc.domain,
                )
            )
        from ..memory.tree import build_tree, render_lines

        tree_lines = render_lines(build_tree(all_docs)) if all_docs else []
        return rows, tree_lines

    def _adoption_times(self, docs: List[MemoryDoc]) -> Dict[str, str]:
        times: Dict[str, str] = {}
        wanted = {d.id for d in docs}
        from ..memory.logstore import event_action, iter_events, latest_feedback_by_token

        feedback_by_token = latest_feedback_by_token(self._store.rsi_dir)
        for event in iter_events(self._store.rsi_dir, all_rolls=True):
            if event.get("kind") == "feedback" and not event.get("retrieved"):
                continue
            action = event_action(event, feedback_by_token)
            if action not in _ADOPTION_ACTIONS:
                continue
            token = event.get("token")
            fb = feedback_by_token.get(str(token)) if token else None
            if not (event.get("action") or event.get("feedback_action")) and fb:
                ts = str(fb.get("ts") or fb.get("created_at") or "")
            else:
                ts = str(event.get("ts") or event.get("created_at") or "")
            for doc_id in event.get("retrieved") or []:
                if doc_id in wanted and ts >= times.get(doc_id, ""):
                    times[doc_id] = ts
        for doc in docs:
            extra_ts = (doc.extra or {}).get("last_adopted_at")
            if extra_ts and str(extra_ts) >= times.get(doc.id, ""):
                times[doc.id] = str(extra_ts)
        return times

    def _trim_prohibitions(self, docs: List[MemoryDoc]) -> List[MemoryDoc]:
        if not docs:
            return []
        adoption = self._adoption_times(docs)
        if adoption:
            ranked = sorted(
                docs,
                key=lambda d: (adoption.get(d.id, ""), d.updated_at or ""),
                reverse=True,
            )
        else:
            ranked = sorted(docs, key=lambda d: d.updated_at or "", reverse=True)
        kept: List[MemoryDoc] = []
        total = 0
        for doc in ranked:
            size = _prohibition_mdc_len(doc.title, doc.content)
            if kept and total + size > MAX_TOTAL_CHARS:
                logger.warning("禁止项拼接超 32KB，裁剪 id=%s", doc.id[:8])
                break
            kept.append(doc)
            total += size
        return kept

    async def current_rules(self, project_id: str) -> List[Dict[str, Any]]:
        """当前会进入宿主上下文的记忆。无项目根时没有载体，返回空列表。"""
        del project_id
        if not self._targets:
            return []
        bundle = self._load_bundle()
        rows: List[Dict[str, Any]] = []
        for row in bundle.prohibitions:
            rows.append({
                "id": row.id, "title": row.title, "content_type": "prohibition",
                "domain": row.domain, "injected": True, "surface": "prohibition",
            })
        for row in bundle.skills:
            rows.append({
                "id": row.id, "title": row.title, "content_type": "skill",
                "domain": row.domain, "injected": True, "surface": "skill",
            })
        for row in bundle.catalog:
            rows.append({
                "id": row.id, "title": row.title, "content_type": row.content_type,
                "domain": row.domain, "injected": True, "surface": "catalog",
            })
        return rows
