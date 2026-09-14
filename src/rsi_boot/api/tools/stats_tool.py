"""rsi_stats 工具（Spec v3.0 §6）：记忆使用统计查询。

period 必填（day/week/month）；group_by 可选（intent/arm）；默认返回 JSON，
export=true 或 format=csv 时写入 ~/.rsi/exports/ 并返回文件路径（§8.3）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ...memory.store import MemoryStore
from ...services.stats_service import StatsService
from ...ux.messages import TOOL_DESC

logger = logging.getLogger(__name__)

TOOL_NAME = "rsi_stats"
TOOL_DESCRIPTION = TOOL_DESC["stats"]

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {"type": "string", "enum": ["day", "week", "month"], "description": "统计周期（必填）"},
        "project_id": {"type": "string", "description": "已废弃：由当前工作区绑定，传入值忽略"},
        "group_by": {"type": "string", "enum": ["intent", "arm"],
                     "description": "分组维度：intent 或召回臂 arm"},
        "format": {"type": "string", "enum": ["json", "csv"], "description": "导出格式（默认 json）"},
        "export": {"type": "boolean", "description": "true 时导出到 ~/.rsi/exports/ 并返回路径"},
    },
    "required": ["period"],
}


async def handle(
    stats: Optional[StatsService], arguments: dict[str, Any], store: MemoryStore | None = None,
) -> dict[str, Any]:
    if store is not None:
        stats = StatsService(store=store)
    if stats is None:
        return {"status": "error", "message": "stats service unavailable"}
    period = str(arguments.get("period") or "")
    if period not in ("day", "week", "month"):
        return {"status": "error", "message": "period 必填（day/week/month）"}
    group_by = arguments.get("group_by") or None
    if group_by is not None and group_by not in ("intent", "arm"):
        return {"status": "error", "message": "group_by 仅支持 intent/arm"}
    fmt = str(arguments.get("format") or "json")
    if fmt not in ("json", "csv"):
        return {"status": "error", "message": "format 仅支持 json/csv"}
    from ...project import bind_project_id

    bound = getattr(stats, "bound_project_id", None)
    project_id = bind_project_id(bound, arguments.get("project_id") or None)
    if project_id == "default" and bound is None and not arguments.get("project_id"):
        project_id = None  # 单测直调未绑定：保持「不过滤」口径

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
        return {"status": "success", "exported": str(path), "summary": payload["summary"]}
    return {"status": "success", **payload}
