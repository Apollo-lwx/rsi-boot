"""rsi_memory_health：扫描、查看和裁决记忆健康发现。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...learning.memory_health import RESOLUTIONS, MemoryHealth
from ...ux.messages import TOOL_DESC

TOOL_NAME = "rsi_memory_health"
TOOL_DESCRIPTION = TOOL_DESC["memory_health"]

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["scan", "list", "explain", "resolve"],
            "description": "scan=立即扫描 list=列出发现 explain=只读展开 resolve=裁决",
        },
        "status": {
            "type": "string",
            "enum": ["open", "awaiting_edit", "snoozed", "resolved"],
            "description": "list 过滤；缺省为 open 与 awaiting_edit",
        },
        "finding_id": {"type": "string", "description": "explain / resolve 必填"},
        "resolution": {
            "type": "string",
            "enum": list(RESOLUTIONS),
            "description": "resolve 必填：archive / revise / keep / snooze",
        },
        "note": {"type": "string", "maxLength": 200, "description": "resolve 可选备注"},
    },
    "required": ["action"],
}


async def handle(runtime: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    action = str(arguments.get("action") or "")
    health = MemoryHealth(runtime.store)
    if action == "scan":
        root = getattr(runtime, "_project_root", None)
        if root is None:
            return {"status": "error", "code": "invalid", "message": "缺少项目根目录"}
        result = health.scan(
            Path(root),
            project_id=str(getattr(runtime, "project_id", "") or ""),
        )
        return {"status": "success", **result}
    if action == "list":
        status = arguments.get("status")
        return health.list_findings(str(status) if status else None)
    if action == "explain":
        finding_id = str(arguments.get("finding_id") or "")
        if not finding_id:
            return {"status": "error", "code": "invalid", "message": "explain 需要 finding_id"}
        return health.explain(finding_id)
    if action == "resolve":
        finding_id = str(arguments.get("finding_id") or "")
        resolution = str(arguments.get("resolution") or "")
        if not finding_id or not resolution:
            return {"status": "error", "code": "invalid", "message": "resolve 需要 finding_id 和 resolution"}
        result = health.resolve(
            finding_id,
            resolution,
            note=str(arguments.get("note") or ""),
            project_root=getattr(runtime, "_project_root", None),
        )
        decisions = getattr(runtime, "decisions", None)
        if decisions is not None and result.get("finding_id") and result.get("code") != "invalid":
            if result.get("status") == "success" or result.get("code") == "conflict":
                decisions.close(str(result["finding_id"]))
        if result.get("status") == "success" and result.get("archived_id"):
            knowledge = getattr(runtime, "knowledge", None)
            if knowledge is not None:
                await knowledge.notify_changed(str(getattr(runtime, "project_id", "") or ""))
        return result
    return {"status": "error", "code": "invalid", "message": f"未知 action: {action}"}
