"""rsi_stats 工具（Spec v3.0 §6）：记忆使用统计查询。

period 必填（day/week/month）；group_by 可选（intent/arm）；默认返回 JSON，
export=true 或 format=csv 时写入 ~/.rsi/exports/ 并返回文件路径（§8.3）。
"""

from __future__ import annotations

import logging
from typing import Any

from ...services.stats_service import StatsService

logger = logging.getLogger(__name__)

TOOL_NAME = "rsi_stats"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {"type": "string", "enum": ["day", "week", "month"], "description": "统计周期（必填）"},
        "project_id": {"type": "string", "description": "项目标识过滤，缺省统计全部项目"},
        "group_by": {"type": "string", "enum": ["intent", "arm"],
                     "description": "分组维度：intent 或召回臂 arm"},
        "format": {"type": "string", "enum": ["json", "csv"], "description": "导出格式（默认 json）"},
        "export": {"type": "boolean", "description": "true 时导出到 ~/.rsi/exports/ 并返回路径"},
    },
    "required": ["period"],
}


async def handle(stats: StatsService, arguments: dict[str, Any]) -> dict[str, Any]:
    period = str(arguments.get("period") or "")
    if period not in ("day", "week", "month"):
        return {"status": "error", "message": "period 必填（day/week/month）"}
    group_by = arguments.get("group_by") or None
    if group_by is not None and group_by not in ("intent", "arm"):
        return {"status": "error", "message": "group_by 仅支持 intent/arm"}
    fmt = str(arguments.get("format") or "json")
    if fmt not in ("json", "csv"):
        return {"status": "error", "message": "format 仅支持 json/csv"}
    project_id = arguments.get("project_id") or None

    payload: dict[str, Any] = {
        "period": period,
        "window_start": stats.window_start(period),
        "project_id": project_id,
        "summary": await stats.summary(period, project_id),
        "trend": await stats.trend(period, project_id),
    }
    if group_by:
        payload["groups"] = await stats.grouped(period, group_by, project_id)

    if bool(arguments.get("export")) or fmt == "csv":
        path = await stats.export(payload, fmt, period)
        return {"status": "ok", "exported": str(path), "summary": payload["summary"]}
    return {"status": "ok", **payload}
