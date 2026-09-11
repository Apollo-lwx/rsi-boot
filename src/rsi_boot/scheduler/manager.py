"""进程内任务调度（§4.3）：asyncio 定时任务，随 serve 启停。

任务表：日志归档（每日，P1.12）、画像重建（每日，P2.4，§3.8 衰减重算）、
策略调优（每周，P3.3，§3.3 衰减 + 淘汰）。
知识提取与 Harness 提案任务在 P3.4/P3.6 注册到同一调度器。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, List, Optional

from ..data.sqlite import SQLiteClient
from ..services.profile_service import ProfileService
from .tasks import archive_old_logs, tune_strategies

logger = logging.getLogger(__name__)

_DAILY_INTERVAL_S = 24 * 3600
_WEEKLY_INTERVAL_S = 7 * 24 * 3600
_FIRST_RUN_DELAY_S = 60


class SchedulerManager:
    def __init__(
        self,
        db: SQLiteClient,
        archive_dir: Path,
        profiles: Optional[ProfileService] = None,
        extractor_provider: Optional[Any] = None,
        proposal_engine_provider: Optional[Any] = None,
        conflict_detector_provider: Optional[Any] = None,
        project_ids: Optional[List[str]] = None,
        global_db: Optional[SQLiteClient] = None,
    ):
        self._db = db
        self._archive_dir = archive_dir
        self._profiles = profiles
        self._global_db = global_db
        # provider 模式：提取器/提案引擎/冲突检测随配置热加载重建，调度器持有 provider 而非实例
        self._extractor_provider = extractor_provider
        self._proposal_engine_provider = proposal_engine_provider
        self._conflict_detector_provider = conflict_detector_provider
        # Harness 任务作用的项目集合（个人工具项目数少，缺省仅 default + 当前项目由调用方给出）
        self._project_ids = [p for p in (project_ids or []) if p]
        self._tasks: List[asyncio.Task] = []

    async def _daily_loop(self) -> None:
        await asyncio.sleep(_FIRST_RUN_DELAY_S)
        while True:
            try:
                archived = await archive_old_logs(self._db, self._archive_dir)
                if archived:
                    logger.info("定时归档完成：%d 条", archived)
            except Exception:
                logger.exception("定时归档失败（下周期重试）")
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
        """§3.9 观察期巡检（每日）：采纳率恶化 > 5% 自动回滚"""
        engine = self._proposal_engine_provider() if self._proposal_engine_provider else None
        if engine is None:
            return
        for project_id in self._project_ids:
            result = await engine.patrol(project_id)
            if result["rolled_back"]:
                logger.warning("观察期自动回滚 %d 个提案（项目 %s）", result["rolled_back"], project_id)

    async def _run_extraction(self) -> None:
        """§4.3 知识提取每日任务（P3.4）：候选汇集 → LLM 提取 → 去重 → 待审队列"""
        extractor = self._extractor_provider() if self._extractor_provider else None
        if extractor is None:
            return
        await extractor.run_daily()

    async def _weekly_loop(self) -> None:
        await asyncio.sleep(_FIRST_RUN_DELAY_S)
        while True:
            try:
                result = await tune_strategies(self._db)
                if result["decayed"] or result["eliminated"]:
                    logger.info("策略调优完成：%s", result)
            except Exception:
                logger.exception("策略调优失败（下周期重试）")
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
        """§4.9 每周全量冲突扫描：捕捉「用户规则随时间过时」（注入重写后的增量扫描由注入器侧触发）"""
        detector = self._conflict_detector_provider() if self._conflict_detector_provider else None
        if detector is None:
            return
        for project_id in self._project_ids:
            result = await detector.scan(project_id)
            if result.get("detected"):
                logger.warning("发现 %d 个用户规则疑似冲突（项目 %s），请用 rsi_conflicts 裁决",
                               result["detected"], project_id)

    async def _run_harness_cycle(self) -> None:
        """§3.9 每周离线任务：失败模式挖掘 → 提案生成 → 回归门禁验证"""
        engine = self._proposal_engine_provider() if self._proposal_engine_provider else None
        if engine is None:
            return
        for project_id in self._project_ids:
            ids = await engine.generate(project_id)
            if ids:
                result = await engine.validate_pending(project_id)
                logger.info("Harness 周期完成（项目 %s）：%s", project_id, result)

    async def _rebuild_profiles(self) -> None:
        """§3.8 离线聚合：对库中所有画像属主执行衰减重算"""
        if self._profiles is None:
            return
        owners: List[Any] = []
        conn = await self._db.connect()
        async with conn.execute("SELECT DISTINCT user_id, project_id FROM user_profiles") as cur:
            owners.extend(await cur.fetchall())
        if self._global_db is not None and self._global_db is not self._db:
            gconn = await self._global_db.connect()
            async with gconn.execute("SELECT DISTINCT user_id, project_id FROM user_profiles") as cur:
                owners.extend(await cur.fetchall())
        for row in owners:
            await self._profiles.rebuild(row["user_id"], row["project_id"] or None)
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
