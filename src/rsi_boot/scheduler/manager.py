"""进程内任务调度（§4.3）：asyncio 定时任务，随 serve 启停。

文件 Runtime：不接收 sqlite。归档/策略调优等必须读库的任务静默跳过。
知识提取与提案/冲突扫描走 store= 后端。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, List, Optional, TYPE_CHECKING

from ..services.profile_service import ProfileService

if TYPE_CHECKING:
    from ..memory.store import MemoryStore

logger = logging.getLogger(__name__)

_DAILY_INTERVAL_S = 24 * 3600
_WEEKLY_INTERVAL_S = 7 * 24 * 3600
_FIRST_RUN_DELAY_S = 60


class SchedulerManager:
    def __init__(
        self,
        archive_dir: Path,
        store: "MemoryStore | None" = None,
        profiles: Optional[ProfileService] = None,
        extractor_provider: Optional[Any] = None,
        proposal_engine_provider: Optional[Any] = None,
        conflict_detector_provider: Optional[Any] = None,
        project_ids: Optional[List[str]] = None,
        **_ignored: Any,
    ):
        self._store = store
        self._archive_dir = archive_dir
        self._profiles = profiles
        self._extractor_provider = extractor_provider
        self._proposal_engine_provider = proposal_engine_provider
        self._conflict_detector_provider = conflict_detector_provider
        self._project_ids = [p for p in (project_ids or []) if p]
        self._tasks: List[asyncio.Task] = []

    async def _daily_loop(self) -> None:
        await asyncio.sleep(_FIRST_RUN_DELAY_S)
        while True:
            try:
                await self._rebuild_profiles()
            except Exception:
                logger.exception("画像重建失败（下周期重试）")
            try:
                await self._run_extraction()
            except Exception:
                logger.exception("知识提取失败（下周期重试）")
            try:
                await self._patrol_proposals()
            except Exception:
                logger.exception("提案观察期巡检失败（下周期重试）")
            await asyncio.sleep(_DAILY_INTERVAL_S)

    async def _patrol_proposals(self) -> None:
        engine = self._proposal_engine_provider() if self._proposal_engine_provider else None
        if engine is None:
            return
        for project_id in self._project_ids:
            result = await engine.patrol(project_id)
            if result["rolled_back"]:
                logger.warning("观察期自动回滚 %d 个提案（项目 %s）", result["rolled_back"], project_id)

    async def _run_extraction(self) -> None:
        extractor = self._extractor_provider() if self._extractor_provider else None
        if extractor is None:
            return
        await extractor.run_daily()

    async def _weekly_loop(self) -> None:
        await asyncio.sleep(_FIRST_RUN_DELAY_S)
        while True:
            try:
                await self._run_harness_cycle()
            except Exception:
                logger.exception("Harness 提案周期失败（下周期重试）")
            try:
                await self._scan_conflicts()
            except Exception:
                logger.exception("规则冲突扫描失败（下周期重试）")
            await asyncio.sleep(_WEEKLY_INTERVAL_S)

    async def _scan_conflicts(self) -> None:
        detector = self._conflict_detector_provider() if self._conflict_detector_provider else None
        if detector is None:
            return
        for project_id in self._project_ids:
            result = await detector.scan(project_id)
            if result.get("detected"):
                logger.warning("发现 %d 个用户规则疑似冲突（项目 %s），请用 rsi_conflicts 裁决",
                               result["detected"], project_id)

    async def _run_harness_cycle(self) -> None:
        engine = self._proposal_engine_provider() if self._proposal_engine_provider else None
        if engine is None:
            return
        for project_id in self._project_ids:
            ids = await engine.generate(project_id)
            if ids:
                result = await engine.validate_pending(project_id)
                logger.info("Harness 周期完成（项目 %s）：%s", project_id, result)

    async def _rebuild_profiles(self) -> None:
        if self._profiles is None:
            return
        owners = self._profiles.list_owners()
        for user_id, project_id in owners:
            await self._profiles.rebuild(user_id, project_id)
        if owners:
            logger.info("画像重建完成：%d 份", len(owners))

    def start(self) -> None:
        if not self._tasks:
            self._tasks = [
                asyncio.create_task(self._daily_loop()),
                asyncio.create_task(self._weekly_loop()),
            ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
