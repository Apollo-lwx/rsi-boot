"""rsi_query 工具（v3.0 起退役）：主链路无生成环节，不在 MCP Server 注册。

按 2026-09-13 readable-memory-rag-dag 设计「保持磁盘存在、不接线」，
本文件仅作存档保留；如需恢复请在 mcp_server.build_server 登记。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ...core.models import RSIRequest, RSIResponse

logger = logging.getLogger(__name__)

TOOL_NAME = "rsi_query"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "raw_input": {"type": "string", "minLength": 1, "maxLength": 32768,
                      "description": "用户原始输入"},
        "project_id": {"type": "string", "maxLength": 128,
                       "description": "项目标识，缺省 default"},
        "role": {"type": "string", "description": "调用方角色（如 developer/reviewer）"},
        "intent": {"type": "string", "description": "显式指定意图，跳过自动识别"},
        "chat_history": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"role": {"type": "string"}, "content": {"type": "string"}},
                      "required": ["role", "content"]},
            "description": "多轮对话历史（最多取最近 10 轮）",
        },
        "preferences": {
            "type": "object",
            "properties": {
                "max_cost": {"type": "number"},
                "latency_first": {"type": "boolean"},
                "force": {"type": "boolean", "description": "覆盖预算超限降级"},
            },
        },
    },
    "required": ["raw_input"],
}


async def handle(runtime: Any, arguments: dict[str, Any]) -> RSIResponse:
    """runtime 为 bootstrap.Runtime；每次调用取当前 pipeline（热加载可能已重建）"""
    args = dict(arguments)
    args.setdefault("request_id", uuid.uuid4().hex)
    args.setdefault("user_id", "local")
    # RSIRequest.chat_history 位于 context 内（§2.1），工具参数平铺以便调用方
    chat_history = args.pop("chat_history", None)
    if chat_history:
        args.setdefault("context", {})["chat_history"] = chat_history
    request = RSIRequest.model_validate(args)
    return await runtime.pipeline.run(request)
