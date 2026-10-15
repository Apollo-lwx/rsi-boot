"""rsi_impact 工具（价值宣讲面板）：把 RSI 的成本/召回价值/行为收益/省 token(A/B)/曲线
组装成一份可导出、经得起追问的报告。省 token 数字只在用户喂入宿主用前/用后用量时才产出。"""

from __future__ import annotations

import asyncio
from typing import Any

from ...ux.messages import TOOL_DESC

TOOL_NAME = "rsi_impact"
TOOL_DESCRIPTION = TOOL_DESC["impact"]

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {"type": "string", "enum": ["day", "week", "month"],
                   "description": "统计窗口（默认 week）"},
        "snapshot": {"type": "boolean",
                     "description": "true 时顺带把当前窗口聚合落一条日快照（攒长期曲线）"},
        "ab": {
            "type": "object",
            "description": "用前/用后对照输入（数字来自宿主自己报的 token 用量，本工具只做差，不估算）",
            "properties": {
                "baseline": {"type": "object", "properties": {
                    "tokens": {"type": "number"}, "tasks": {"type": "number"}}},
                "treatment": {"type": "object", "properties": {
                    "tokens": {"type": "number"}, "tasks": {"type": "number"}}},
            },
        },
        "export": {"type": "boolean", "description": "true 时导出一页纸 markdown 到 ~/.rsi/exports/ 并返回路径"},
    },
    "required": [],
}


async def handle(runtime: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    from ...project import tool_project_id
    from ...services.impact_service import ImpactService

    period = str(arguments.get("period") or "week")
    if period not in ("day", "week", "month"):
        period = "week"
    project_id = tool_project_id(runtime, arguments)
    if project_id == "default" and getattr(runtime, "project_id", None) is None \
            and not arguments.get("project_id"):
        project_id = None  # 单测直调未绑定：保持「不过滤」口径
    service = ImpactService(runtime.store, project_root=getattr(runtime, "_project_root", None))
    ab = arguments.get("ab") if isinstance(arguments.get("ab"), dict) else None

    def _build() -> dict[str, Any]:
        if arguments.get("snapshot"):
            service.snapshot_now(project_id)
        return service.report(period, project_id, ab)

    try:
        payload = await asyncio.to_thread(_build)
    except Exception as exc:  # 面板兜底，不因观测异常中断 MCP 调用
        return {"status": "error", "code": "impact_failed", "message": str(exc)}

    if arguments.get("export"):
        path = await asyncio.to_thread(service.export_markdown, payload)
        return {"status": "success", "exported": str(path), "headline": payload["headline"]}
    return {"status": "success", "data": payload}
