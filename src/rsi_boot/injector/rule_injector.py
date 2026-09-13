"""规则文件注入编排（Spec v3.0 §4.2）：记忆变更 → 全量幂等重写 → 台账同步。

触发点：知识 add/review/delete、提案晋升/回滚（经 KnowledgeService.on_change 与
ProposalEngine 回调注入）。同一项目的重写经 asyncio.Lock 串行化，多变更事件合并。
注入内容过三道闸：§8.1 脱敏（写入库前已执行）+ 审批闸门（仅 active 注入）+
黑名单过滤（本类执行）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..data.sqlite import SQLiteClient
from .blocklist import is_blocked
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

#: 参与注入的记忆类型（legacy 'experience' 按 convention 处理）
_INJECT_TYPES = ("prohibition", "convention", "experience")
_ADOPTION_ACTIONS = frozenset({"accepted", "applied", "copied", "referenced"})


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prohibition_mdc_len(title: str, content: str) -> int:
    return len(_mdc(title, "", True, content[:MAX_FILE_CHARS]))


class RuleInjector:
    def __init__(
        self,
        db: Optional[SQLiteClient] = None,
        project_root: Optional[Path] = None,
        store: MemoryStore | None = None,
    ):
        self._db = db
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
        lock_key = project_id or (str(self._store.rsi_dir) if self._store is not None else "")
        lock = self._locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            bundle = await self._load_bundle(project_id)
            written: List[Artifact] = []
            for target in self._targets:
                try:
                    written.extend(target.write(bundle))
                except OSError as exc:
                    # §8 韧性：规则文件不可写不阻塞主链路，下轮事件重试
                    logger.error("载体 %s 写入失败（下轮变更重试）: %s", target.name, exc)
            if self._store is not None or self._db is None:
                removed = 0
            else:
                removed = await self._sync_ledger(project_id, written)
            logger.info("规则注入完成（项目 %s）：%d 个文件，移除 %d 个", project_id, len(written), removed)
            return {"written": len(written), "removed": removed}

    async def _load_bundle(self, project_id: str) -> MemoryBundle:
        if self._store is not None:
            return self._load_bundle_from_store()
        return await self._load_bundle_from_db(project_id)

    async def _load_bundle_from_db(self, project_id: str) -> MemoryBundle:
        conn = await self._db.connect()
        placeholders = ",".join("?" for _ in _INJECT_TYPES)
        async with conn.execute(
            f"SELECT id, title, content, content_type, domain FROM knowledge_items "
            f"WHERE project_id = ? AND status = 'active' AND content_type IN ({placeholders}) "
            f"ORDER BY updated_at DESC",
            (project_id, *_INJECT_TYPES),
        ) as cur:
            rows = await cur.fetchall()
        bundle = MemoryBundle()
        for r in rows:
            # 第三道闸：黑名单内容拒绝注入（不脱库——库中保留供审计，仅不进入宿主上下文）
            if is_blocked(f"{r['title']}\n{r['content']}"):
                logger.warning("记忆 %s 命中注入黑名单，跳过注入", r["id"][:8])
                continue
            row = MemoryRow(id=r["id"], title=r["title"], content=r["content"],
                            content_type=r["content_type"], domain=r["domain"])
            if r["content_type"] == "prohibition":
                bundle.prohibitions.append(row)
            else:
                bundle.conventions.append(row)
        return bundle

    def _load_bundle_from_store(self) -> MemoryBundle:
        """正式 prohibitions/ + skills/*/skill.yaml 的 name/description；不注入 convention。"""
        store = self._store
        assert store is not None
        docs = [d for d in store.list_official("prohibition") if d.status == "active"]
        kept = self._trim_prohibitions(docs)
        bundle = MemoryBundle()
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
        return bundle

    def _adoption_times(self, docs: List[MemoryDoc]) -> Dict[str, str]:
        times: Dict[str, str] = {}
        wanted = {d.id for d in docs}
        try:
            from ..memory.logstore import iter_events
        except ImportError:
            return times
        for event in iter_events(self._store.rsi_dir):
            action = event.get("action") or event.get("feedback_action")
            if action not in _ADOPTION_ACTIONS:
                continue
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

    async def _sync_ledger(self, project_id: str, written: List[Artifact]) -> int:
        """rule_artifacts 台账同步：产出 upsert 为 active；台账有而本次未产出 → removed"""
        conn = await self._db.connect()
        now = _utc_iso()
        seen = set()
        for art in written:
            seen.add((art.target, art.rel_path))
            await conn.execute(
                "INSERT INTO rule_artifacts (id, project_id, target, target_path, source_item_id,"
                " content_hash, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)"
                " ON CONFLICT(project_id, target, target_path) DO UPDATE SET"
                " source_item_id = excluded.source_item_id, content_hash = excluded.content_hash,"
                " status = 'active', updated_at = excluded.updated_at",
                (uuid.uuid4().hex, project_id, art.target, art.rel_path,
                 art.source_item_id, art.content_hash, now, now),
            )
        async with conn.execute(
            "SELECT id, target, target_path FROM rule_artifacts WHERE project_id = ? AND status = 'active'",
            (project_id,),
        ) as cur:
            existing = await cur.fetchall()
        removed = 0
        for row in existing:
            if (row["target"], row["target_path"]) not in seen:
                await conn.execute(
                    "UPDATE rule_artifacts SET status = 'removed', updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                removed += 1
        await conn.commit()
        return removed

    async def current_rules(self, project_id: str) -> List[Dict[str, Any]]:
        """审计查询：宿主上下文中当前有哪些记忆（台账 active 行）"""
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT target, target_path, source_item_id, content_hash, updated_at"
            " FROM rule_artifacts WHERE project_id = ? AND status = 'active' ORDER BY target, target_path",
            (project_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]
