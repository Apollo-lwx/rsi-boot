"""记忆使用统计服务（Spec v3.0 §6，A6）：触达/采纳/禁止项遵循/提案通过率。

v3.0 口径重写：主链路零模型调用，不再统计 token/成本；数据来自 interaction_logs
（intent='recall' 行）、rule_artifacts（注入台账）与 harness_proposals。
仅统计终态记录。导出走 ~/.rsi/exports/（§8.3 数据生命周期，数据不出本地）。

禁止项遵循率为近似口径：召回响应摘录含「禁止：」标题 = 禁止项触达的召回；
其中反馈为 rejected/modified 或 rating ≤ 2 视为违反信号（宿主输出仍踩坑）。
"""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import yaml

from ..data.sqlite import SQLiteClient
from ..memory.logstore import iter_events

if TYPE_CHECKING:
    from ..memory.store import MemoryStore

logger = logging.getLogger(__name__)

#: period → 覆盖天数（day=今天，week=近 7 天，month=近 30 天；UTC 自然日对齐）
_PERIODS: Dict[str, int] = {"day": 1, "week": 7, "month": 30}

_GROUP_COLUMNS = {"intent": "intent", "arm": "strategy_name"}

_POSITIVE_ACTIONS = ("accepted", "applied", "copied", "referenced")
_NEGATIVE_ACTIONS = ("rejected", "modified")


class StatsService:
    def __init__(self, db: Optional[SQLiteClient] = None, store: MemoryStore | None = None):
        if store is None and db is None:
            raise TypeError("db is required when store is omitted")
        self._db = db
        self._store = store

    @staticmethod
    def window_start(period: str) -> str:
        """统计窗口起点（UTC 自然日 00:00 对齐的 ISO8601）"""
        days = _PERIODS[period]
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return (today - timedelta(days=days - 1)).isoformat()

    def _where(self, period: str, project_id: Optional[str]) -> Tuple[str, list]:
        sql = "created_at >= ? AND status != 'pending'"
        params: list = [self.window_start(period)]
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        return sql, params

    async def summary(self, period: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        if self._store is not None:
            return self._summary_store(period, project_id)
        where, params = self._where(period, project_id)
        conn = await self._db.connect()

        # 召回量与延迟
        async with conn.execute(
            f"SELECT COUNT(*) AS n, AVG(latency_ms) AS avg_lat FROM interaction_logs"
            f" WHERE intent = 'recall' AND {where}",
            params,
        ) as cur:
            row = await cur.fetchone()
        recalls = int(row["n"])
        avg_latency = round(float(row["avg_lat"]), 1) if row["avg_lat"] is not None else None

        # 采纳率：正反馈 / 全部反馈（recall 行）
        async with conn.execute(
            f"SELECT feedback_action AS a, COUNT(*) AS n FROM interaction_logs"
            f" WHERE intent = 'recall' AND feedback_action IS NOT NULL AND {where} GROUP BY a",
            params,
        ) as cur:
            fb_rows = await cur.fetchall()
        fb_total = sum(int(r["n"]) for r in fb_rows)
        fb_positive = sum(int(r["n"]) for r in fb_rows if r["a"] in _POSITIVE_ACTIONS)
        adoption = round(fb_positive / fb_total, 4) if fb_total else None

        # 禁止项遵循率（近似口径，见模块 docstring）
        async with conn.execute(
            f"SELECT COUNT(*) AS n FROM interaction_logs"
            f" WHERE intent = 'recall' AND response_excerpt LIKE '%禁止：%' AND {where}",
            params,
        ) as cur:
            surfaced = int((await cur.fetchone())["n"])
        async with conn.execute(
            f"SELECT COUNT(*) AS n FROM interaction_logs"
            f" WHERE intent = 'recall' AND response_excerpt LIKE '%禁止：%'"
            f" AND (feedback_action IN ('rejected', 'modified')"
            f"      OR (feedback_rating IS NOT NULL AND feedback_rating <= 2)) AND {where}",
            params,
        ) as cur:
            violated = int((await cur.fetchone())["n"])
        compliance = round(1 - violated / surfaced, 4) if surfaced else None

        # 触达：规则注入产物（当前 active 台账）+ 召回次数
        artifact_sql = "SELECT COUNT(*) AS n FROM rule_artifacts WHERE status = 'active'"
        artifact_params: list = []
        if project_id:
            artifact_sql += " AND project_id = ?"
            artifact_params.append(project_id)
        async with conn.execute(artifact_sql, artifact_params) as cur:
            artifacts_active = int((await cur.fetchone())["n"])

        # 提案通过率：窗口内已裁决提案中 approved/active 占比
        prop_sql = ("SELECT status, COUNT(*) AS n FROM harness_proposals"
                    " WHERE status IN ('approved', 'active', 'rejected') AND created_at >= ?")
        prop_params: list = [self.window_start(period)]
        if project_id:
            prop_sql += " AND project_id = ?"
            prop_params.append(project_id)
        prop_sql += " GROUP BY status"
        async with conn.execute(prop_sql, prop_params) as cur:
            prop_rows = await cur.fetchall()
        prop_total = sum(int(r["n"]) for r in prop_rows)
        prop_passed = sum(int(r["n"]) for r in prop_rows if r["status"] in ("approved", "active"))
        proposal_pass = round(prop_passed / prop_total, 4) if prop_total else None

        # 记忆库存量
        item_sql = ("SELECT content_type, status, COUNT(*) AS n FROM knowledge_items"
                    + (" WHERE project_id = ?" if project_id else "") + " GROUP BY content_type, status")
        async with conn.execute(item_sql, (project_id,) if project_id else ()) as cur:
            item_rows = await cur.fetchall()
        inventory = {
            f"{r['content_type'] or 'unknown'}/{r['status']}": int(r["n"]) for r in item_rows
        }

        return {
            "recalls": recalls,
            "avg_recall_latency_ms": avg_latency,
            "adoption_rate": adoption,
            "prohibition_compliance": compliance,
            "prohibition_surfaced": surfaced,
            "rule_artifacts_active": artifacts_active,
            "proposal_pass_rate": proposal_pass,
            "memory_inventory": inventory,
        }

    async def grouped(self, period: str, group_by: str, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """分组：intent 或召回臂（arm=strategy_name）；各组召回次数与采纳率"""
        if self._store is not None:
            return self._grouped_store(period, group_by, project_id)
        column = _GROUP_COLUMNS[group_by]
        where, params = self._where(period, project_id)
        conn = await self._db.connect()
        async with conn.execute(
            f"""
            SELECT COALESCE({column}, '(unknown)') AS key,
                   COUNT(*) AS recalls,
                   SUM(CASE WHEN feedback_action IN ('accepted','applied','copied','referenced')
                            THEN 1 ELSE 0 END) AS positive,
                   SUM(CASE WHEN feedback_action IS NOT NULL THEN 1 ELSE 0 END) AS feedbacks
            FROM interaction_logs WHERE intent = 'recall' AND {where}
            GROUP BY key ORDER BY recalls DESC
            """,
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                group_by: r["key"],
                "recalls": int(r["recalls"]),
                "adoption_rate": round(int(r["positive"]) / int(r["feedbacks"]), 4)
                if int(r["feedbacks"]) else None,
            }
            for r in rows
        ]

    async def trend(self, period: str, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """时间序列：day 按小时分桶，week/month 按日分桶（created_at 为 ISO8601 UTC TEXT，前缀截取即分桶）"""
        if self._store is not None:
            return self._trend_store(period, project_id)
        bucket_len = 13 if period == "day" else 10
        where, params = self._where(period, project_id)
        conn = await self._db.connect()
        async with conn.execute(
            f"""
            SELECT substr(created_at, 1, {bucket_len}) AS bucket,
                   COUNT(*) AS recalls,
                   SUM(CASE WHEN feedback_action IN ('accepted','applied','copied','referenced')
                            THEN 1 ELSE 0 END) AS positive,
                   SUM(CASE WHEN feedback_action IS NOT NULL THEN 1 ELSE 0 END) AS feedbacks
            FROM interaction_logs WHERE intent = 'recall' AND {where}
            GROUP BY bucket ORDER BY bucket
            """,
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "bucket": r["bucket"],
                "recalls": int(r["recalls"]),
                "adoption_rate": round(int(r["positive"]) / int(r["feedbacks"]), 4)
                if int(r["feedbacks"]) else None,
            }
            for r in rows
        ]

    async def export(self, payload: Dict[str, Any], fmt: str, period: str) -> Path:
        """导出到 ~/.rsi/exports/（§8.3），返回文件路径"""
        exports_dir = Path(os.environ.get("RSI_HOME", str(Path.home() / ".rsi"))) / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = exports_dir / f"rsi_stats_{period}_{stamp}.{fmt}"
        if fmt == "json":
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        else:
            self._write_csv(path, payload)
        logger.info("记忆使用统计已导出：%s", path)
        return path

    @staticmethod
    def _write_csv(path: Path, payload: Dict[str, Any]) -> None:
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["metric", "value"])
            for key, value in payload["summary"].items():
                if isinstance(value, dict):
                    for k2, v2 in value.items():
                        writer.writerow([f"{key}.{k2}", v2])
                else:
                    writer.writerow([key, value])
            writer.writerow([])
            for section in ("groups", "trend"):
                rows = payload.get(section)
                if not rows:
                    continue
                writer.writerow([section])
                writer.writerow(list(rows[0].keys()))
                for row in rows:
                    writer.writerow(list(row.values()))
                writer.writerow([])

    def _event_ts(self, event: Dict[str, Any]) -> str:
        return str(event.get("ts") or event.get("created_at") or "")

    def _event_action(self, event: Dict[str, Any]) -> Optional[str]:
        return event.get("action") or event.get("feedback_action")

    def _event_rating(self, event: Dict[str, Any]) -> Optional[int]:
        raw = event.get("rating")
        if raw is None:
            raw = event.get("feedback_rating")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _event_excerpt(self, event: Dict[str, Any]) -> str:
        return str(event.get("excerpt") or event.get("response_excerpt") or "")

    def _recall_events(self, period: str, project_id: Optional[str]) -> List[Dict[str, Any]]:
        assert self._store is not None
        start = self.window_start(period)
        out: List[Dict[str, Any]] = []
        for event in iter_events(self._store.rsi_dir, kinds={"recall"}):
            if event.get("status") == "pending":
                continue
            ts = self._event_ts(event)
            if ts and ts < start:
                continue
            if project_id and event.get("project_id") and event.get("project_id") != project_id:
                continue
            out.append(event)
        return out

    def _load_yaml_list(self, path: Path, key: str) -> List[Dict[str, Any]]:
        if not path.is_file():
            return []
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return []
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            rows = data.get(key) or data.get("items") or []
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
        return []

    def _proposal_rows(self) -> List[Dict[str, Any]]:
        assert self._store is not None
        root = self._store.rsi_dir / "state" / "proposals"
        if not root.is_dir():
            return []
        rows: List[Dict[str, Any]] = []
        for path in sorted(root.glob("*.yaml")):
            if path.name.endswith(".tmp"):
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(data, dict):
                rows.append(data)
        return rows

    def _summary_store(self, period: str, project_id: Optional[str]) -> Dict[str, Any]:
        events = self._recall_events(period, project_id)
        recalls = len(events)
        lats = [float(e["latency_ms"]) for e in events if e.get("latency_ms") is not None]
        avg_latency = round(sum(lats) / len(lats), 1) if lats else None
        fb = [self._event_action(e) for e in events if self._event_action(e)]
        fb_positive = sum(1 for a in fb if a in _POSITIVE_ACTIONS)
        adoption = round(fb_positive / len(fb), 4) if fb else None

        surfaced_events = [e for e in events if "禁止：" in self._event_excerpt(e)]
        surfaced = len(surfaced_events)
        violated = 0
        for event in surfaced_events:
            action = self._event_action(event)
            rating = self._event_rating(event)
            if action in _NEGATIVE_ACTIONS or (rating is not None and rating <= 2):
                violated += 1
        compliance = round(1 - violated / surfaced, 4) if surfaced else None

        artifacts = self._load_yaml_list(self._store.rsi_dir / "state" / "artifacts.yaml", "artifacts")
        if project_id:
            artifacts = [r for r in artifacts if not r.get("project_id") or r.get("project_id") == project_id]
        artifacts_active = sum(1 for r in artifacts if r.get("status") == "active")

        start = self.window_start(period)
        props = []
        for row in self._proposal_rows():
            if row.get("status") not in ("approved", "active", "rejected"):
                continue
            created = str(row.get("created_at") or "")
            if created and created < start:
                continue
            if project_id and row.get("project_id") and row.get("project_id") != project_id:
                continue
            props.append(row)
        prop_passed = sum(1 for r in props if r.get("status") in ("approved", "active"))
        proposal_pass = round(prop_passed / len(props), 4) if props else None

        inventory: Dict[str, int] = {}
        for doc in [*self._store.list_official(), *self._store.list_pending()]:
            key = f"{doc.type or 'unknown'}/{doc.status}"
            inventory[key] = inventory.get(key, 0) + 1

        return {
            "recalls": recalls,
            "avg_recall_latency_ms": avg_latency,
            "adoption_rate": adoption,
            "prohibition_compliance": compliance,
            "prohibition_surfaced": surfaced,
            "rule_artifacts_active": artifacts_active,
            "proposal_pass_rate": proposal_pass,
            "memory_inventory": inventory,
        }

    def _grouped_store(
        self, period: str, group_by: str, project_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        field = "intent" if group_by == "intent" else "arm"
        buckets: Dict[str, Dict[str, int]] = {}
        for event in self._recall_events(period, project_id):
            key = str(event.get(field) or event.get("strategy_name") or "(unknown)")
            if group_by == "intent" and key == "(unknown)":
                key = str(event.get("kind") or "recall")
            slot = buckets.setdefault(key, {"recalls": 0, "positive": 0, "feedbacks": 0})
            slot["recalls"] += 1
            action = self._event_action(event)
            if action:
                slot["feedbacks"] += 1
                if action in _POSITIVE_ACTIONS:
                    slot["positive"] += 1
        rows = []
        for key, slot in buckets.items():
            rows.append({
                group_by: key,
                "recalls": slot["recalls"],
                "adoption_rate": round(slot["positive"] / slot["feedbacks"], 4)
                if slot["feedbacks"] else None,
            })
        rows.sort(key=lambda r: r["recalls"], reverse=True)
        return rows

    def _trend_store(self, period: str, project_id: Optional[str]) -> List[Dict[str, Any]]:
        bucket_len = 13 if period == "day" else 10
        buckets: Dict[str, Dict[str, int]] = {}
        for event in self._recall_events(period, project_id):
            ts = self._event_ts(event)
            if not ts:
                continue
            bucket = ts[:bucket_len]
            slot = buckets.setdefault(bucket, {"recalls": 0, "positive": 0, "feedbacks": 0})
            slot["recalls"] += 1
            action = self._event_action(event)
            if action:
                slot["feedbacks"] += 1
                if action in _POSITIVE_ACTIONS:
                    slot["positive"] += 1
        return [
            {
                "bucket": key,
                "recalls": slot["recalls"],
                "adoption_rate": round(slot["positive"] / slot["feedbacks"], 4)
                if slot["feedbacks"] else None,
            }
            for key, slot in sorted(buckets.items())
        ]
