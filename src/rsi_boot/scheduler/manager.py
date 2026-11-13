"""进程内任务调度（§4.3）：asyncio 定时任务，随 serve 启停。

文件 Runtime：事件按保留期归档到 archive/，召回臂在周任务里衰减并淘汰。
知识提取与提案/冲突扫描走 store= 后端。

中断语义（docs/specs/2026-11-13-learning-loop-recovery.md）：两个循环都会在
serve 启动 60s 后首跑，因此每个任务经 `_run_due` 记录成功时间——重启只补跑
「到期且上次未成功」的任务，周期任务不因频繁重启重复执行（decay_arms 每调用
必衰减，重复执行会过度衰减，属必须防的 outward action）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, TYPE_CHECKING

import yaml

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
        project_root: Optional[Path] = None,
        **_ignored: Any,
    ):
        self._store = store
        self._project_root = project_root
        self._archive_dir = archive_dir
        self._profiles = profiles
        self._extractor_provider = extractor_provider
        self._proposal_engine_provider = proposal_engine_provider
        self._conflict_detector_provider = conflict_detector_provider
        self._project_ids = [p for p in (project_ids or []) if p]
        self._tasks: List[asyncio.Task] = []

    # ---- 中断语义：成功才记录，失败/中断的任务在下次到期（含重启首跑）重试 ----

    def _runs_path(self) -> Optional[Path]:
        if self._store is None:
            return None
        return self._store.rsi_dir / "state" / "scheduler-runs.yaml"

    def _last_runs(self) -> Dict[str, str]:
        path = self._runs_path()
        if path is None or not path.is_file():
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return {}
        return data if isinstance(data, dict) else {}

    def _mark_run(self, name: str) -> None:
        path = self._runs_path()
        if path is None:
            return
        runs = self._last_runs()
        runs[name] = datetime.now(timezone.utc).isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(yaml.safe_dump(runs, allow_unicode=True), encoding="utf-8")
        tmp.replace(path)

    def _due(self, name: str, interval_s: float) -> bool:
        last = self._last_runs().get(name)
        if not last:
            return True
        try:
            dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        except ValueError:
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() >= interval_s

    async def _run_due(
        self, name: str, interval_s: float, fn: Callable[[], Awaitable[None]],
    ) -> None:
        """到期才执行；成功落 last-run 记录。异常不吞：由外层循环捕获记日志。"""
        if not self._due(name, interval_s):
            return
        await fn()
        await asyncio.to_thread(self._mark_run, name)

    async def _daily_loop(self) -> None:
        await asyncio.sleep(_FIRST_RUN_DELAY_S)
        while True:
            try:
                await self._run_due("profiles", _DAILY_INTERVAL_S, self._rebuild_profiles)
            except Exception:
                logger.exception("画像重建失败（下周期重试）")
            try:
                await self._run_due("extraction", _DAILY_INTERVAL_S, self._run_extraction)
            except Exception:
                logger.exception("知识提取失败（下周期重试）")
            try:
                await self._run_due("proposal_patrol", _DAILY_INTERVAL_S, self._patrol_proposals)
            except Exception:
                logger.exception("提案观察期巡检失败（下周期重试）")
            try:
                await self._run_due("event_archive", _DAILY_INTERVAL_S, self._archive_events)
            except Exception:
                logger.exception("事件归档失败（下周期重试）")
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
                await self._run_due("recall_arms", _WEEKLY_INTERVAL_S, self._decay_recall_arms)
            except Exception:
                logger.exception("召回臂遗忘失败（下周期重试）")
            try:
                await self._run_due("harness_cycle", _WEEKLY_INTERVAL_S, self._run_harness_cycle)
            except Exception:
                logger.exception("Harness 提案周期失败（下周期重试）")
            try:
                await self._run_due("conflict_scan", _WEEKLY_INTERVAL_S, self._scan_conflicts)
            except Exception:
                logger.exception("规则冲突扫描失败（下周期重试）")
            await asyncio.sleep(_WEEKLY_INTERVAL_S)

    async def _archive_events(self) -> None:
        if self._store is None:
            return
        from ..memory.logstore import archive_expired_events

        archived = await asyncio.to_thread(
            archive_expired_events, self._store.rsi_dir, self._archive_dir,
        )
        if archived:
            logger.info("已归档 %d 条过期事件到 %s", archived, self._archive_dir)

    async def _decay_recall_arms(self) -> None:
        if self._store is None:
            return
        from ..strategy.recall import decay_arms, eliminate_arms

        path = self._store.rsi_dir / "state" / "arms.yaml"
        await asyncio.to_thread(decay_arms, path)
        eliminated = await asyncio.to_thread(eliminate_arms, path)
        if eliminated:
            logger.warning("召回臂淘汰 %d 条（曝光达标且采纳率过低）", eliminated)

    async def _scan_conflicts(self) -> None:
        detector = self._conflict_detector_provider() if self._conflict_detector_provider else None
        if detector is None:
            return
        for project_id in self._project_ids:
            result = await detector.scan(project_id)
            if result.get("detected"):
                logger.warning("发现 %d 个用户规则疑似冲突（项目 %s），请用 rsi_conflicts 裁决",
                               result["detected"], project_id)

    async def _scan_memory_health(self) -> None:
        if self._store is None or self._project_root is None:
            return
        from ..learning.memory_health import MemoryHealth

        project_id = self._project_ids[0] if self._project_ids else ""
        try:
            result = MemoryHealth(self._store).scan(self._project_root, project_id=project_id)
        except Exception:
            logger.exception("记忆健康扫描失败（下周期重试）")
            return
        if any(result.get(key) for key in ("opened", "refreshed", "closed")):
            logger.info("记忆健康扫描：%s", result)

    async def _run_harness_cycle(self) -> None:
        await self._scan_memory_health()
        engine = self._proposal_engine_provider() if self._proposal_engine_provider else None
        if engine is None:
            return
        for project_id in self._project_ids:
            ids = await engine.generate(project_id)
            result = await engine.validate_pending(project_id)
            if ids or result.get("validated"):
                logger.info(
                    "Harness 周期完成（项目 %s）：生成 %s，校验 %s",
                    project_id, ids, result,
                )

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
