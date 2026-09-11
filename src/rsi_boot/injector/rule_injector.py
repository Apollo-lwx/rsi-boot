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
from typing import Any, Dict, List, Optional

from ..data.sqlite import SQLiteClient
from .blocklist import is_blocked
from .targets import AgentsMdTarget, Artifact, CursorRuleTarget, MemoryBundle, MemoryRow, RuleTarget

logger = logging.getLogger(__name__)

#: 参与注入的记忆类型（legacy 'experience' 按 convention 处理）
_INJECT_TYPES = ("prohibition", "convention", "experience")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuleInjector:
    def __init__(self, db: SQLiteClient, project_root: Optional[Path] = None):
        self._db = db
        self._targets: List[RuleTarget] = []
        if project_root is not None:
            self._targets = [CursorRuleTarget(project_root), AgentsMdTarget(project_root)]
        self._locks: Dict[str, asyncio.Lock] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._targets)

    async def rewrite(self, project_id: str) -> Dict[str, int]:
        """全量重写该项目全部载体的规则产物并同步台账。无载体（无项目根）时静默跳过"""
        if not self._targets:
            return {"written": 0, "removed": 0}
        lock = self._locks.setdefault(project_id, asyncio.Lock())
        async with lock:
            bundle = await self._load_bundle(project_id)
            written: List[Artifact] = []
            for target in self._targets:
                try:
                    written.extend(target.write(bundle))
                except OSError as exc:
                    # §8 韧性：规则文件不可写不阻塞主链路，下轮事件重试
                    logger.error("载体 %s 写入失败（下轮变更重试）: %s", target.name, exc)
            removed = await self._sync_ledger(project_id, written)
            logger.info("规则注入完成（项目 %s）：%d 个文件，移除 %d 个", project_id, len(written), removed)
            return {"written": len(written), "removed": removed}

    async def _load_bundle(self, project_id: str) -> MemoryBundle:
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
