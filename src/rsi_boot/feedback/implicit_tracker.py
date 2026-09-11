"""隐式反馈追踪与异步反馈处理（§4.2，P2.5）。

隐式行为上报协议：采纳/修改/复制/应用等 IDE 侧行为由客户端通过 rsi_feedback 上报
（action 为对应隐式动作，携带 feedback_token）。MCP 协议本身不感知用户行为，
由 IDE 集成层负责捕获并上报；无客户端集成时仅统计显式反馈。

反馈处理投递到进程内 asyncio.Queue（§2.4，maxsize=1000），由 FeedbackWorker 后台
消费，不阻塞主推理链路。进程退出时队列中未消费的反馈丢失——增量信号，可接受；
显式反馈（rating/rejected）在 MCP 响应返回前已同步落库日志，仅画像/策略更新走队列。

字段级映射（§4.2）：reward 喂给 §3.3 Thompson 更新（alpha/beta）；
画像的 intent 计数在请求时由管线记录（§3.8），反馈侧不重复计数；
modified 且 diff > 20% 的知识提取候选队列在 Phase 3 接入（§4.3）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Dict, Optional

from pydantic import BaseModel, Field

from ..core.models import FeedbackAction
from ..data.sqlite import SQLiteClient
from ..quality.assessor import QualityAssessor
from ..services.log_service import LogService
from ..services.profile_service import ProfileService

logger = logging.getLogger(__name__)

#: 隐式动作（客户端上报）与显式动作（用户主动）全集
IMPLICIT_ACTIONS = {
    FeedbackAction.ACCEPTED.value,
    FeedbackAction.APPLIED.value,
    FeedbackAction.MODIFIED.value,
    FeedbackAction.COPIED.value,
    FeedbackAction.REFERENCED.value,
    FeedbackAction.IGNORED.value,
}
ALL_ACTIONS = IMPLICIT_ACTIONS | {FeedbackAction.REJECTED.value}

#: 反馈 → 召回臂 reward 映射（Spec v3.0 §4.7；v2.6 §4.2 映射随 rsi_query 退役废止）
ACTION_REWARD: Dict[str, float] = {
    "accepted": 2.0,
    "applied": 2.0,
    "copied": 2.0,
    "referenced": 2.0,
    "modified": -0.5,
    "rejected": -1.0,
    "ignored": 0.0,
}

DIFF_CANDIDATE_THRESHOLD = 0.2  # modified diff 比例 > 20% → 知识提取候选（Phase 3）


class ImplicitEvent(BaseModel):
    """客户端上报的反馈事件（隐式动作 + 可选修改后内容/评分/评语）"""

    feedback_token: str = Field(..., min_length=1)
    action: str
    modified_content: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = None


def diff_ratio(original: str, modified: str) -> float:
    """修改占比：1 - 序列相似度（0 = 未改，1 = 全改）"""
    if original == modified:
        return 0.0
    if not original or not modified:
        return 1.0
    return 1.0 - SequenceMatcher(None, original, modified).ratio()


def rating_reward(rating: Optional[int]) -> float:
    """§4.2：4-5 分 +1；1-2 分 -1；3 分 0"""
    if rating is None:
        return 0.0
    if rating >= 4:
        return 1.0
    if rating <= 2:
        return -1.0
    return 0.0


class FeedbackWorker:
    """asyncio.Queue 消费者：校验日志上下文 → Thompson 策略更新（§3.3/§4.2）"""

    def __init__(
        self,
        db: SQLiteClient,
        maxsize: int = 1000,
        quality: Optional[QualityAssessor] = None,
        profiles: Optional[ProfileService] = None,
    ):
        self._db = db
        self._quality = quality
        self._profiles = profiles
        self._logs = LogService(db)
        self.queue: asyncio.Queue[ImplicitEvent] = asyncio.Queue(maxsize=maxsize)
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def submit(self, event: ImplicitEvent) -> bool:
        """非阻塞入队；队列满时丢弃（增量信号丢失可接受，§4.2）"""
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            logger.warning("反馈队列已满，丢弃事件 action=%s", event.action)
            return False

    async def _consume(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                await self._process(event)
            except Exception:
                logger.exception("反馈处理失败 action=%s", event.action)
            finally:
                self.queue.task_done()

    async def _process(self, event: ImplicitEvent) -> None:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT id, user_id, project_id, intent, strategy_name, raw_input, retrieved_tags "
            "FROM interaction_logs WHERE feedback_token = ?",
            (event.feedback_token,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            logger.warning("反馈事件 token 无对应日志，跳过")
            return

        # copied/referenced：该次检索命中标签 +1（§4.2 画像更新列）
        if event.action in ("copied", "referenced") and self._profiles is not None:
            tags = json.loads(row["retrieved_tags"]) if row["retrieved_tags"] else []
            if tags:
                await self._profiles.record_interest_boost(
                    row["user_id"], row["project_id"] or "default", tags,
                )

        # Thompson 更新：action 与 rating reward 叠加（§4.2「分别生效」数值等价）
        reward = ACTION_REWARD.get(event.action, 0.0) + rating_reward(event.rating)
        if reward != 0.0 and row["strategy_name"]:
            await self._apply_reward(row["project_id"] or "default", row["intent"], row["strategy_name"], reward)

        # modified diff > 20% → 知识提取候选入库（§4.4 规则式提取，每日任务汇集）
        if event.action == "modified" and event.modified_content:
            ratio = diff_ratio(row["raw_input"] or "", event.modified_content)
            if ratio > DIFF_CANDIDATE_THRESHOLD:
                await self._enqueue_candidate(row, "modified", event.modified_content)
                logger.info("知识提取候选已入队：diff_ratio=%.2f", ratio)

        # rejected + comment → 禁止项提取候选（§4.4：comment 全文前缀规范化「禁止：」）
        if event.action == "rejected" and event.comment:
            await self._enqueue_candidate(row, "rejected", event.comment)
            logger.info("禁止项提取候选已入队（rejected+comment）")

        # 差评必查（§3.5）：rating ≤ 2 补做 accuracy 评判并回写 quality_score
        if event.rating is not None and event.rating <= 2 and self._quality is not None:
            qresult = await self._quality.judge_negative_feedback(event.feedback_token)
            if qresult is not None:
                await self._logs.refresh_quality_score(event.feedback_token, qresult.quality_score)
                logger.info(
                    "差评 accuracy 必查完成：score=%.2f reason=%s",
                    qresult.accuracy or 0.0, qresult.judge_reason or "",
                )

    async def _enqueue_candidate(self, log_row: dict, candidate_type: str, answer: str) -> None:
        """候选写入 extraction_candidates（UNIQUE 约束幂等，重复反馈不重复入队）"""
        conn = await self._db.connect()
        await conn.execute(
            "INSERT OR IGNORE INTO extraction_candidates"
            " (id, project_id, source_log_id, candidate_type, question, answer, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                uuid.uuid4().hex,
                log_row["project_id"] or "default",
                log_row["id"],
                candidate_type,
                log_row["raw_input"] or "",
                answer[:8000],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await conn.commit()

    async def _apply_reward(self, project_id: str, intent: Optional[str], strategy_name: str, reward: float) -> None:
        """§3.3：reward > 0 → alpha += reward；< 0 → beta += |reward|；兜底策略（未落库）跳过"""
        conn = await self._db.connect()
        now = datetime.now(timezone.utc).isoformat()
        if reward > 0:
            sql = "UPDATE strategy_configs SET alpha = alpha + ?, updated_at = ?"
        else:
            sql = "UPDATE strategy_configs SET beta = beta + ?, updated_at = ?"
        cur = await conn.execute(
            sql + " WHERE project_id = ? AND intent = ? AND strategy_name = ? AND is_active = 1",
            (abs(reward), now, project_id, intent or "", strategy_name),
        )
        await conn.commit()
        if cur.rowcount:
            logger.info("策略 %s reward %+.1f 已应用", strategy_name, reward)
