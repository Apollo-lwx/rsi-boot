"""rsi_recall 工具（Spec v3.0 §6）：记忆召回入口，描述驱动宿主自动调用。"""

from __future__ import annotations

from typing import Any

TOOL_NAME = "rsi_recall"

#: 工具描述契约：驱动宿主在任务开始前自动调用（A/B 调优的载体，改动需同步 mcp_server 注册处）
TOOL_DESCRIPTION = (
    "在开始任何编码、适配、调试、设计任务前调用，获取本项目历史沉淀的经验、约定与禁止项"
    "（prohibitions 为必须遵守的禁止项，items 为相关经验）。返回 feedback_token 用于后续"
    "通过 rsi_feedback 上报采纳情况。"
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "minLength": 1, "maxLength": 8192,
                 "description": "即将开始的任务描述（自然语言）"},
        "project_id": {"type": "string", "maxLength": 128,
                       "description": "项目标识，缺省 default"},
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
    project_id = str(arguments.get("project_id") or "default")
    payload = await runtime.recall.recall(
        task,
        project_id,
        user_id=str(arguments.get("user_id") or "local"),
        role=arguments.get("role"),
        top_k=arguments.get("top_k"),
    )
    return {"status": "success", "data": payload}
