"""记忆召回服务（Spec v3.0 §4.3）：rsi_recall 的内核。

召回排序：禁止项命中一律置顶（不受分数门槛限制）→ 其余走检索管线（召回臂参数）。
每次召回落一行 interaction_logs（intent='recall'，token 字段为 0），feedback_token
照常签发——反馈闭环据此驱动召回臂学习与采纳率统计。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.models import KnowledgeItem, RSIRequest, generate_feedback_token
from ..data.sqlite import SQLiteClient
from ..knowledge.retriever import KnowledgeRetriever
from ..strategy.recall import RecallArmSelector
from .decision_queue import DecisionQueue, collect_decision_cards
from .log_service import LogService
from .profile_service import ProfileService

logger = logging.getLogger(__name__)

_RECALL_SCAN_LIMIT = 200  # 禁止项匹配的项目内扫描上限


class RecallService:
    def __init__(
        self,
        db: SQLiteClient,
        retriever: KnowledgeRetriever,
        arms: RecallArmSelector,
        feedback_secret: str,
        profiles: Optional[ProfileService] = None,
        bound_project_id: Optional[str] = None,
        decisions: Optional[DecisionQueue] = None,
        project_root: Optional[Path] = None,
    ):
        self._db = db
        self._retriever = retriever
        self._arms = arms
        self._secret = feedback_secret
        self._profiles = profiles
        self.bound_project_id = bound_project_id
        self._decisions = decisions if decisions is not None else DecisionQueue()
        self._project_root = project_root
        self._logs = LogService(db)
        self._bg_tasks: set[asyncio.Task] = set()  # 画像增量后台任务，close 前 drain

    async def recall(
        self,
        task: str,
        project_id: str,
        user_id: str = "local",
        role: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        from ..project import bind_project_id

        project_id = bind_project_id(self.bound_project_id, project_id)
        started = time.monotonic()
        arm = await self._arms.pick(project_id)
        limit = min(top_k, 10) if top_k else arm.top_n

        # 禁止项：FTS/bigram 命中一律置顶，不受分数门槛限制（§4.3）
        prohibitions = await self._match_prohibitions(task, project_id, role)
        # 其余记忆：检索管线（召回臂 top_n/threshold 生效）
        items = await self._retriever.search(
            task, project_id, role=role, top_k=limit, threshold=arm.threshold,
        )
        items = [i for i in items if i.content_type != "prohibition"]  # 禁止项已由置顶通道承载

        latency_ms = int((time.monotonic() - started) * 1000)
        token = await self._log_recall(
            task, project_id, user_id, role, arm.name, prohibitions, items, latency_ms,
        )
        self._spawn_profile_hits(user_id, project_id, prohibitions + items)

        cards = await collect_decision_cards(
            self._db, project_id, project_root=self._project_root,
        )
        picked = self._decisions.pick(cards)
        decisions: list[dict[str, Any]] = []
        if picked is not None:
            picked.more_waiting = self._decisions.remaining_after(cards, picked)
            self._decisions.mark_presented(picked.id)
            decisions = [picked.asdict()]

        return {
            "prohibitions": [
                {"title": p.title, "content": p.content, "domain": p.domain} for p in prohibitions
            ],
            "items": [
                {"title": i.title, "content": i.content, "content_type": i.content_type, "tags": i.tags}
                for i in items
            ],
            "recall_arm": arm.name,
            "feedback_token": token,
            "decisions": decisions,
        }

    async def _match_prohibitions(
        self, task: str, project_id: str, role: Optional[str]
    ) -> List[KnowledgeItem]:
        """项目 active 禁止项中与 task 有词项重叠者（≥2 命中防单词噪声；单词查询放宽到 1）"""
        terms = KnowledgeRetriever._cjk_terms(task)
        if not terms:
            return []
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM knowledge_items WHERE project_id = ? AND status = 'active'"
            " AND content_type = 'prohibition' LIMIT ?",
            (project_id, _RECALL_SCAN_LIMIT),
        ) as cur:
            rows = await cur.fetchall()
        min_hits = 1 if len(terms) == 1 else 2
        matched: List[KnowledgeItem] = []
        for row in rows:
            roles = row["roles"] or "[]"
            if roles != "[]" and role and role not in roles:
                continue
            text = f"{row['title']}\n{row['content']}".lower()
            if sum(1 for t in terms if t in text) >= min_hits:
                matched.append(KnowledgeRetriever._row_to_item(row))
        return matched

    async def _log_recall(
        self,
        task: str,
        project_id: str,
        user_id: str,
        role: Optional[str],
        arm_name: str,
        prohibitions: List[KnowledgeItem],
        items: List[KnowledgeItem],
        latency_ms: int,
    ) -> str:
        """召回事件落库（intent='recall'，无模型调用 token 恒 0）；response_excerpt 存命中
        标题清单——rating ≥ 4 的召回由此进入知识提取候选（§4.4 高分问答对）"""
        request = RSIRequest(user_id=user_id, project_id=project_id, role=role, raw_input=task)
        token = generate_feedback_token(request.request_id, user_id, self._secret)
        log_id = await self._logs.insert_pending(request, token)
        hits = prohibitions + items
        await self._logs.finalize(
            log_id, status="success", intent="recall", strategy_name=arm_name,
            latency_ms=latency_ms,
            response_excerpt="\n".join(h.title for h in hits) if hits else None,
            retrieved_tags=[t for h in hits for t in ([h.domain] if h.domain else []) + list(h.tags)],
        )
        return token

    def _spawn_profile_hits(
        self, user_id: str, project_id: str, hits: List[KnowledgeItem]
    ) -> None:
        """画像增量（§4.8 输入源）：recall 命中计数后台记录，不阻塞召回"""
        if self._profiles is None or not hits:
            return

        async def _update() -> None:
            assert self._profiles is not None
            await self._profiles.record_retrieval_hits(user_id, project_id, hits)

        task = asyncio.create_task(_update())
        self._bg_tasks.add(task)

        def _on_done(t: asyncio.Task) -> None:
            self._bg_tasks.discard(t)
            if not t.cancelled() and t.exception():
                logger.warning("画像命中计数失败: %s", t.exception())

        task.add_done_callback(_on_done)

    async def drain(self, timeout: float = 2.0) -> None:
        """等待后台画像任务完成（Runtime.close 在关库前调用，防写已关闭连接）"""
        pending = [t for t in self._bg_tasks if not t.done()]
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout)
            except asyncio.TimeoutError:
                logger.warning("画像后台任务 drain 超时（%d 个未完成）", len(pending))
