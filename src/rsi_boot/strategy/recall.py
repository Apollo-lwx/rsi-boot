"""召回策略臂（Spec v3.0 §4.7）：Thompson Sampling 作用于召回参数组合。

臂存 strategy_configs（intent='recall' 维度）：params JSON 承载 top_n/threshold，
alpha/beta/exposure_count 沿用 §3.3 机制（FeedbackWorker 按 reward 更新，
scheduler/tasks.py 周衰减与淘汰）。项目首次召回时播种三个默认臂。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..data.sqlite import SQLiteClient
from ..memory.store import MemoryStore

logger = logging.getLogger(__name__)

RECALL_INTENT = "recall"
_CACHE_TTL_S = 60.0

#: 默认召回臂（Spec §4.7 参数表）
DEFAULT_ARMS = [
    ("recall-conservative", {"top_n": 3, "threshold": 0.7}),
    ("recall-balanced", {"top_n": 5, "threshold": 0.6}),
    ("recall-generous", {"top_n": 8, "threshold": 0.5}),
]


@dataclass
class RecallArm:
    id: str
    name: str
    top_n: int
    threshold: float
    alpha: float
    beta: float


class RecallArmSelector:
    """召回臂的播种、Thompson 选择、曝光计数；运行时读取走 60s 内存缓存"""

    def __init__(self, db: Optional[SQLiteClient] = None, store: MemoryStore | None = None):
        if store is None and db is None:
            raise TypeError("db is required when store is omitted")
        self._db = db
        self._store = store
        self._cache: Dict[str, tuple[float, List[RecallArm]]] = {}

    async def pick(self, project_id: str) -> RecallArm:
        arms = await self._load_arms(project_id)
        arm = arms[0] if len(arms) == 1 else max(
            arms, key=lambda a: random.betavariate(max(a.alpha, 1e-3), max(a.beta, 1e-3))
        )
        self._spawn_exposure(arm.id)
        return arm

    async def _load_arms(self, project_id: str) -> List[RecallArm]:
        cached = self._cache.get(project_id)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_S:
            return cached[1]

        if self._store is not None:
            rows = self._load_arms_store(project_id)
            arms = [self._to_arm_row(r) for r in rows]
            self._cache[project_id] = (time.monotonic(), arms)
            return arms

        conn = await self._db.connect()
        async with conn.execute(
            "SELECT id, strategy_name, params, alpha, beta FROM strategy_configs "
            "WHERE project_id = ? AND intent = ? AND is_active = 1",
            (project_id, RECALL_INTENT),
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            await self._seed(project_id)
            async with conn.execute(
                "SELECT id, strategy_name, params, alpha, beta FROM strategy_configs "
                "WHERE project_id = ? AND intent = ? AND is_active = 1",
                (project_id, RECALL_INTENT),
            ) as cur:
                rows = await cur.fetchall()

        arms = [self._to_arm(r) for r in rows]
        self._cache[project_id] = (time.monotonic(), arms)
        return arms

    async def _seed(self, project_id: str) -> None:
        conn = await self._db.connect()
        now = datetime.now(timezone.utc).isoformat()
        for name, params in DEFAULT_ARMS:
            await conn.execute(
                "INSERT INTO strategy_configs (id, project_id, intent, role, strategy_name,"
                " model_name, template_ref, weight, params, is_active, created_at, updated_at)"
                " VALUES (?, ?, ?, NULL, ?, 'none', NULL, 1.0, ?, 1, ?, ?)",
                (uuid.uuid4().hex, project_id, RECALL_INTENT, name, json.dumps(params), now, now),
            )
        await conn.commit()
        logger.info("项目 %s 召回臂已播种（%d 个）", project_id, len(DEFAULT_ARMS))

    @staticmethod
    def _to_arm(row: Any) -> RecallArm:
        params = json.loads(row["params"] or "{}")
        return RecallArm(
            id=row["id"], name=row["strategy_name"],
            top_n=int(params.get("top_n", 5)), threshold=float(params.get("threshold", 0.6)),
            alpha=float(row["alpha"]), beta=float(row["beta"]),
        )

    def _arms_path(self) -> Path:
        assert self._store is not None
        return self._store.rsi_dir / "state" / "arms.yaml"

    def _read_all_arm_rows(self) -> List[Dict[str, Any]]:
        path = self._arms_path()
        if not path.is_file():
            return []
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return []
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            rows = data.get("arms") or []
            return [r for r in rows if isinstance(r, dict)]
        return []

    def _write_all_arm_rows(self, rows: List[Dict[str, Any]]) -> None:
        import os
        dest = self._arms_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(dest) + ".tmp")
        tmp.write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(tmp, dest)

    def _is_recall_arm(self, row: Dict[str, Any]) -> bool:
        intent = row.get("intent") or RECALL_INTENT
        return intent == RECALL_INTENT

    def _is_active_arm(self, row: Dict[str, Any]) -> bool:
        if "active" in row:
            return bool(row.get("active"))
        if "is_active" in row:
            return bool(row.get("is_active"))
        return True

    def _load_arms_store(self, project_id: str) -> List[Dict[str, Any]]:
        rows = self._read_all_arm_rows()
        recall = [r for r in rows if self._is_recall_arm(r) and self._is_active_arm(r)]
        if not recall:
            now = datetime.now(timezone.utc).isoformat()
            for name, params in DEFAULT_ARMS:
                rows.append({
                    "id": uuid.uuid4().hex,
                    "name": name,
                    "intent": RECALL_INTENT,
                    "top_n": params["top_n"],
                    "threshold": params["threshold"],
                    "alpha": 1.0,
                    "beta": 1.0,
                    "exposure": 0,
                    "active": True,
                    "project_id": project_id,
                    "params": params,
                    "created_at": now,
                    "updated_at": now,
                })
            self._write_all_arm_rows(rows)
            recall = [r for r in rows if self._is_recall_arm(r) and self._is_active_arm(r)]
        return recall

    @staticmethod
    def _to_arm_row(row: Dict[str, Any]) -> RecallArm:
        params = row.get("params") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (TypeError, ValueError):
                params = {}
        return RecallArm(
            id=str(row.get("id") or uuid.uuid4().hex),
            name=str(row.get("name") or row.get("strategy_name") or "recall-balanced"),
            top_n=int(row.get("top_n") or params.get("top_n", 5)),
            threshold=float(row.get("threshold") or params.get("threshold", 0.6)),
            alpha=float(row.get("alpha") if row.get("alpha") is not None else 1.0),
            beta=float(row.get("beta") if row.get("beta") is not None else 1.0),
        )

    def _spawn_exposure(self, arm_id: str) -> None:
        async def _incr() -> None:
            if self._store is not None:
                rows = self._read_all_arm_rows()
                now = datetime.now(timezone.utc).isoformat()
                for row in rows:
                    if row.get("id") == arm_id:
                        row["exposure"] = int(row.get("exposure") or row.get("exposure_count") or 0) + 1
                        row["updated_at"] = now
                        break
                self._write_all_arm_rows(rows)
                return
            conn = await self._db.connect()
            await conn.execute(
                "UPDATE strategy_configs SET exposure_count = exposure_count + 1, updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), arm_id),
            )
            await conn.commit()

        task = asyncio.create_task(_incr())
        task.add_done_callback(self._on_bg_done)

    @staticmethod
    def _on_bg_done(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception():
            logger.warning("召回臂曝光计数失败: %s", t.exception())

    def invalidate_cache(self) -> None:
        self._cache.clear()
