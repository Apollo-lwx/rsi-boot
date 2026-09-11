"""策略引擎（§3.3）：确定路由 + Thompson Sampling 概率探索。

选择：同一 intent 下多条候选策略为 arm，从各 arm 的 Beta(alpha, beta) 后验采样，
取采样值最大者（纯 Thompson，无需 epsilon——小样本 arm 方差大，探索自然发生）。
角色专属策略（role 匹配）与通用策略（role IS NULL）分池：角色池非空只在角色池采样。

状态：alpha/beta/exposure_count 存 strategy_configs；运行时读取走内存缓存
（60s 刷新，不阻塞主链路）；曝光计数选中即 +1（异步任务，不阻塞响应）。
更新：reward 由 FeedbackWorker 应用（§4.2）；周衰减与淘汰见 scheduler/tasks.py。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..data.sqlite import SQLiteClient
from .cost_router import CostRouter

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 60.0  # §3.3：运行时读取走内存缓存（60s 刷新）


@dataclass
class StrategyDecision:
    strategy_name: str
    model_name: str
    template_ref: str
    strategy_id: Optional[str] = None  # None = 兜底默认策略（未落库）


@dataclass
class _Arm:
    id: str
    strategy_name: str
    model_name: str
    template_ref: Optional[str]
    alpha: float
    beta: float


class StrategyEngine:
    def __init__(self, db: SQLiteClient, config: dict[str, Any]):
        self._db = db
        self._config = config
        self._cost_router = CostRouter(config)  # P4.2：默认关闭，仅作用于兜底路由
        self._cache: Dict[Tuple[str, str, str], Tuple[float, List[_Arm], List[_Arm]]] = {}

    async def decide(self, project_id: str, intent: str, role: Optional[str] = None) -> StrategyDecision:
        role_arms, generic_arms = await self._load_arms(project_id, intent, role)
        pool = role_arms or generic_arms
        if pool:
            arm = self._thompson_pick(pool)
            self._spawn_exposure(arm.id)
            return StrategyDecision(
                strategy_name=arm.strategy_name,
                model_name=arm.model_name,
                template_ref=arm.template_ref or f"{intent}.jinja2",
                strategy_id=arm.id,
            )

        # 兜底路由：成本路由开启时按意图价格层级选 cheapest-in-tier（§6.2，P4.2）
        default_model = self._cost_router.route(intent)
        return StrategyDecision(
            strategy_name="default",
            model_name=default_model,
            template_ref=f"{intent}.jinja2",
        )

    # ---------- Thompson Sampling ----------

    @staticmethod
    def _thompson_pick(arms: List[_Arm]) -> _Arm:
        """Beta 后验采样取最大；单候选直接返回（采样退化为确定）"""
        if len(arms) == 1:
            return arms[0]
        return max(arms, key=lambda a: random.betavariate(max(a.alpha, 1e-3), max(a.beta, 1e-3)))

    async def _load_arms(
        self, project_id: str, intent: str, role: Optional[str]
    ) -> Tuple[List[_Arm], List[_Arm]]:
        key = (project_id, intent, role or "")
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_S:
            return cached[1], cached[2]

        conn = await self._db.connect()
        sql = (
            "SELECT id, strategy_name, model_name, template_ref, alpha, beta, role "
            "FROM strategy_configs WHERE project_id = ? AND intent = ? AND is_active = 1 "
            "AND (role = ? OR role IS NULL)"
        )
        async with conn.execute(sql, (project_id, intent, role)) as cur:
            rows = await cur.fetchall()
        role_arms = [self._to_arm(r) for r in rows if r["role"]]
        generic_arms = [self._to_arm(r) for r in rows if not r["role"]]
        self._cache[key] = (time.monotonic(), role_arms, generic_arms)
        return role_arms, generic_arms

    @staticmethod
    def _to_arm(row: Any) -> _Arm:
        return _Arm(
            id=row["id"],
            strategy_name=row["strategy_name"],
            model_name=row["model_name"],
            template_ref=row["template_ref"],
            alpha=float(row["alpha"]),
            beta=float(row["beta"]),
        )

    def _spawn_exposure(self, strategy_id: str) -> None:
        """曝光计数 +1：后台任务不阻塞主链路；失败仅记日志"""

        async def _incr() -> None:
            from datetime import datetime, timezone

            conn = await self._db.connect()
            await conn.execute(
                "UPDATE strategy_configs SET exposure_count = exposure_count + 1, updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), strategy_id),
            )
            await conn.commit()

        task = asyncio.create_task(_incr())
        task.add_done_callback(self._on_bg_done)

    @staticmethod
    def _on_bg_done(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception():
            logger.warning("策略曝光计数失败: %s", t.exception())

    def invalidate_cache(self) -> None:
        """配置/策略变更后调用（热加载、提案晋升）"""
        self._cache.clear()
