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
import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Dict, Optional

import yaml
from pydantic import BaseModel, Field

from ..core.models import FeedbackAction
from ..learning.pipeline import extract_from_feedback
from ..memory.logstore import iter_events
from ..memory.store import MemoryStore
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
        store: MemoryStore,
        maxsize: int = 1000,
        quality: Optional[QualityAssessor] = None,
        profiles: Optional[ProfileService] = None,
    ):
        self._store = store
        self._quality = quality
        self._profiles = profiles
        self._logs = LogService(store=store)
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
        await self._process_store(event)

    async def _process_store(self, event: ImplicitEvent) -> None:
        row = None
        for ev in iter_events(self._store.rsi_dir):
            if ev.get("token") == event.feedback_token and ev.get("kind") != "feedback":
                row = ev
        if row is None:
            logger.warning("反馈事件 token 无对应日志，跳过")
            return
        retrieved = [str(x) for x in (row.get("retrieved") or [])]
        legacy = [str(x) for x in (row.get("retrieved_legacy_tags") or [])]
        tags = retrieved + legacy
        if event.action in ("copied", "referenced") and self._profiles is not None and tags:
            await self._profiles.record_interest_boost(
                row.get("user_id") or "local", row.get("project_id") or "default", tags,
            )
        reward = ACTION_REWARD.get(event.action, 0.0) + rating_reward(event.rating)
        arm = row.get("arm") or row.get("strategy_name")
        if reward != 0.0 and arm:
            await self._apply_reward_store(row.get("project_id") or "default", arm, reward)
        question = row.get("task") or row.get("raw_input") or ""
        should_extract = False
        if event.action == "rejected" and event.comment:
            should_extract = True
        elif event.action == "modified" and event.modified_content:
            should_extract = diff_ratio(question, event.modified_content) > DIFF_CANDIDATE_THRESHOLD
        if should_extract:
            written = extract_from_feedback(
                self._store.rsi_dir,
                action=event.action,
                comment=event.comment,
                retrieved=retrieved,
                modified_content=event.modified_content,
                question=question,
            )
            if written:
                logger.info("反馈提取已写入 pending：action=%s n=%s", event.action, len(written))
        if event.rating is not None and event.rating <= 2 and self._quality is not None:
            qresult = await self._quality.judge_negative_feedback(event.feedback_token)
            if qresult is not None:
                await self._logs.refresh_quality_score(event.feedback_token, qresult.quality_score)

    async def _apply_reward_store(self, project_id: str, strategy_name: str, reward: float) -> None:
        path = self._store.rsi_dir / "state" / "arms.yaml"
        if not path.is_file():
            return
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return
        rows = data if isinstance(data, list) else (data or {}).get("arms") or []
        now = datetime.now(timezone.utc).isoformat()
        changed = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("name") or row.get("strategy_name")
            if name != strategy_name:
                continue
            if row.get("active") is False or row.get("is_active") == 0:
                continue
            if reward > 0:
                row["alpha"] = float(row.get("alpha") or 1.0) + reward
            else:
                row["beta"] = float(row.get("beta") or 1.0) + abs(reward)
            row["updated_at"] = now
            changed = True
        if changed:
            dest = path
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_text(yaml.safe_dump(rows if isinstance(data, list) else {"arms": rows},
                                          allow_unicode=True, sort_keys=False), encoding="utf-8")
            tmp.replace(dest)
            logger.info("策略 %s reward %+.1f 已应用", strategy_name, reward)
