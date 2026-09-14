"""rsi_recall 工具（Spec v3.0 §6）：记忆召回入口，描述驱动宿主自动调用。"""

from __future__ import annotations

from typing import Any

from ...ux.messages import TOOL_DESC

TOOL_NAME = "rsi_recall"

TOOL_DESCRIPTION = TOOL_DESC["recall"]

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "minLength": 1, "maxLength": 8192,
                 "description": "即将开始的任务描述（自然语言）"},
        "project_id": {"type": "string", "maxLength": 128,
                       "description": "已废弃：由当前工作区绑定，传入值忽略"},
        "role": {"type": "string", "description": "调用方角色（如 developer/test/pm）"},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 10,
                  "description": "返回条数上限（缺省由召回策略臂决定）"},
    },
    "required": ["task"],
}


async def handle(runtime: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    task = str(arguments.get("task", "")).strip()
    if not task:
        return {"status": "error", "message": "task 不能为空"}
    from ...project import tool_project_id

    project_id = tool_project_id(runtime, arguments)
    payload = await runtime.recall.recall(
        task,
        project_id,
        user_id=str(arguments.get("user_id") or "local"),
        role=arguments.get("role"),
        top_k=arguments.get("top_k"),
    )
    return {"status": "success", "data": payload}
