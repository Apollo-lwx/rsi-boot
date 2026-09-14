"""MCP stdio server（Spec v3.0 §6）：对外暴露 rsi_recall / rsi_feedback / rsi_knowledge_* /
rsi_review / rsi_conflicts / rsi_stats / rsi_learn / rsi_memory 工具族。
v3.0 起 rsi_query 退役（主链路无生成环节）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .. import __version__
from ..services.log_service import LogService
from ..services.stats_service import StatsService
from ..ux.messages import TOOL_DESC
from .tools import (
    conflicts_tool,
    feedback_tool,
    knowledge_add_tool,
    knowledge_review_tool,
    knowledge_search_tool,
    knowledge_tool,
    learn_tool,
    memory_tool,
    recall_tool,
    review_tool,
    stats_tool,
)

logger = logging.getLogger(__name__)

_INSTRUCTIONS = TOOL_DESC["recall"]


def build_server(runtime: Any, logs: LogService, feedback_secret: str) -> Server:
    """runtime 为 bootstrap.Runtime（持有热加载后的当前组件）"""
    # initialize 握手：声明版本与能力说明（§7 协议契约，P1.15）
    server: Server = Server("rsi-boot", version=__version__, instructions=_INSTRUCTIONS)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=recall_tool.TOOL_NAME,
                       description=recall_tool.TOOL_DESCRIPTION,
                       inputSchema=recall_tool.INPUT_SCHEMA),
            types.Tool(name=feedback_tool.TOOL_NAME,
                       description=feedback_tool.TOOL_DESCRIPTION,
                       inputSchema=feedback_tool.INPUT_SCHEMA),
            types.Tool(name=knowledge_add_tool.TOOL_NAME,
                       description=knowledge_add_tool.TOOL_DESCRIPTION,
                       inputSchema=knowledge_add_tool.INPUT_SCHEMA),
            types.Tool(name=knowledge_search_tool.TOOL_NAME,
                       description=knowledge_search_tool.TOOL_DESCRIPTION,
                       inputSchema=knowledge_search_tool.INPUT_SCHEMA),
            types.Tool(name=knowledge_tool.TOOL_NAME,
                       description=knowledge_tool.TOOL_DESCRIPTION,
                       inputSchema=knowledge_tool.INPUT_SCHEMA),
            types.Tool(name=knowledge_review_tool.TOOL_NAME,
                       description=knowledge_review_tool.TOOL_DESCRIPTION,
                       inputSchema=knowledge_review_tool.INPUT_SCHEMA),
            types.Tool(name=review_tool.TOOL_NAME,
                       description=review_tool.TOOL_DESCRIPTION,
                       inputSchema=review_tool.INPUT_SCHEMA),
            types.Tool(name=conflicts_tool.TOOL_NAME,
                       description=conflicts_tool.TOOL_DESCRIPTION,
                       inputSchema=conflicts_tool.INPUT_SCHEMA),
            types.Tool(name=stats_tool.TOOL_NAME,
                       description=stats_tool.TOOL_DESCRIPTION,
                       inputSchema=stats_tool.INPUT_SCHEMA),
            types.Tool(name=learn_tool.TOOL_NAME,
                       description=learn_tool.TOOL_DESCRIPTION,
                       inputSchema=learn_tool.INPUT_SCHEMA),
            types.Tool(name=memory_tool.TOOL_NAME,
                       description=memory_tool.TOOL_DESCRIPTION,
                       inputSchema=memory_tool.INPUT_SCHEMA),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        try:
            if name == recall_tool.TOOL_NAME:
                payload = await recall_tool.handle(runtime, arguments)
            elif name == feedback_tool.TOOL_NAME:
                payload = await feedback_tool.handle(logs, feedback_secret, arguments, worker=runtime.feedback_worker)
            elif name == knowledge_add_tool.TOOL_NAME:
                payload = await knowledge_add_tool.handle(runtime.knowledge, arguments)
            elif name == knowledge_search_tool.TOOL_NAME:
                payload = await knowledge_search_tool.handle(runtime.knowledge, arguments)
            elif name == knowledge_tool.TOOL_NAME:
                payload = await knowledge_tool.handle(runtime.knowledge, arguments)
            elif name == knowledge_review_tool.TOOL_NAME:
                payload = await knowledge_review_tool.handle(runtime, arguments)
            elif name == review_tool.TOOL_NAME:
                payload = await review_tool.handle(runtime, arguments)
            elif name == conflicts_tool.TOOL_NAME:
                payload = await conflicts_tool.handle(runtime, arguments)
            elif name == stats_tool.TOOL_NAME:
                stats = StatsService(store=runtime.store)
                stats.bound_project_id = runtime.project_id
                payload = await stats_tool.handle(stats, arguments)
            elif name == learn_tool.TOOL_NAME:
                payload = await learn_tool.handle(runtime, arguments)
            elif name == memory_tool.TOOL_NAME:
                payload = await memory_tool.handle(runtime, arguments)
            else:
                payload = {"status": "error", "code": "invalid", "message": f"unknown tool: {name}"}
        except Exception as exc:  # 工具级兜底，避免 MCP 连接中断
            logger.exception("tool %s failed", name)
            payload = {"status": "error", "code": "invalid", "message": str(exc)}
        return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))]

    return server


async def serve(runtime: Any, logs: LogService, feedback_secret: str) -> None:
    server = build_server(runtime, logs, feedback_secret)
    runtime.feedback_worker.start()  # §4.2 异步反馈消费，随 serve 启停
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await runtime.feedback_worker.stop()
