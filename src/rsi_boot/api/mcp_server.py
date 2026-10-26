"""MCP stdio server（Spec v3.0 §6）：对外暴露 rsi_recall / rsi_feedback / rsi_knowledge_* /
rsi_review / rsi_conflicts / rsi_memory_health / rsi_stats / rsi_learn / rsi_memory 工具族。
v3.0 起 rsi_query 退役（主链路无生成环节）。
"""

from __future__ import annotations

import json
import logging
import sys
from io import TextIOWrapper
from typing import Any, AsyncIterator

import anyio
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .. import __version__
from ..services.stats_service import StatsService
from ..ux.messages import TOOL_DESC
from .tools import (
    conflicts_tool,
    explore_tool,
    feedback_tool,
    impact_tool,
    knowledge_add_tool,
    knowledge_delete_tool,
    knowledge_review_tool,
    knowledge_search_tool,
    knowledge_update_tool,
    learn_tool,
    memory_health_tool,
    memory_tool,
    recall_tool,
    review_tool,
    stats_tool,
)

logger = logging.getLogger(__name__)

_INSTRUCTIONS = TOOL_DESC["recall"]


def build_server(session: Any) -> Server:
    """session 为 __main__.ServeSession（首个请求时按 MCP roots 定锚并构建 runtime）"""
    # initialize 握手：声明版本与能力说明（§7 协议契约，P1.15）
    server: Server = Server("rsi-boot", version=__version__, instructions=_INSTRUCTIONS)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        # 握手后客户端立刻拉工具清单：借此时机定锚工作区并构建 runtime，
        # 保证注入产物在 agent 读 AGENTS.md 前落盘；构建失败不阻塞工具清单返回。
        try:
            await session.ensure_started(server)
        except Exception:
            logger.exception("runtime 构建失败（list_tools 仍返回工具清单）")
        return [
            types.Tool(name=recall_tool.TOOL_NAME,
                       description=recall_tool.TOOL_DESCRIPTION,
                       inputSchema=recall_tool.INPUT_SCHEMA),
            types.Tool(name=explore_tool.TOOL_NAME,
                       description=explore_tool.TOOL_DESCRIPTION,
                       inputSchema=explore_tool.INPUT_SCHEMA),
            types.Tool(name=feedback_tool.TOOL_NAME,
                       description=feedback_tool.TOOL_DESCRIPTION,
                       inputSchema=feedback_tool.INPUT_SCHEMA),
            types.Tool(name=knowledge_add_tool.TOOL_NAME,
                       description=knowledge_add_tool.TOOL_DESCRIPTION,
                       inputSchema=knowledge_add_tool.INPUT_SCHEMA),
            types.Tool(name=knowledge_search_tool.TOOL_NAME,
                       description=knowledge_search_tool.TOOL_DESCRIPTION,
                       inputSchema=knowledge_search_tool.INPUT_SCHEMA),
            types.Tool(name=knowledge_delete_tool.TOOL_NAME,
                       description=knowledge_delete_tool.TOOL_DESCRIPTION,
                       inputSchema=knowledge_delete_tool.INPUT_SCHEMA),
            types.Tool(name=knowledge_update_tool.TOOL_NAME,
                       description=knowledge_update_tool.TOOL_DESCRIPTION,
                       inputSchema=knowledge_update_tool.INPUT_SCHEMA),
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
            types.Tool(name=impact_tool.TOOL_NAME,
                       description=impact_tool.TOOL_DESCRIPTION,
                       inputSchema=impact_tool.INPUT_SCHEMA),
            types.Tool(name=learn_tool.TOOL_NAME,
                       description=learn_tool.TOOL_DESCRIPTION,
                       inputSchema=learn_tool.INPUT_SCHEMA),
            types.Tool(name=memory_health_tool.TOOL_NAME,
                       description=memory_health_tool.TOOL_DESCRIPTION,
                       inputSchema=memory_health_tool.INPUT_SCHEMA),
            types.Tool(name=memory_tool.TOOL_NAME,
                       description=memory_tool.TOOL_DESCRIPTION,
                       inputSchema=memory_tool.INPUT_SCHEMA),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        try:
            runtime = await session.ensure_started(server)
            logs = runtime.logs
            feedback_secret = runtime.feedback_secret
            if name == recall_tool.TOOL_NAME:
                payload = await recall_tool.handle(runtime, arguments)
            elif name == explore_tool.TOOL_NAME:
                payload = await explore_tool.handle(runtime, arguments)
            elif name == feedback_tool.TOOL_NAME:
                payload = await feedback_tool.handle(logs, feedback_secret, arguments, worker=runtime.feedback_worker)
            elif name == knowledge_add_tool.TOOL_NAME:
                payload = await knowledge_add_tool.handle(runtime.knowledge, arguments)
            elif name == knowledge_search_tool.TOOL_NAME:
                payload = await knowledge_search_tool.handle(runtime.knowledge, arguments)
            elif name == knowledge_delete_tool.TOOL_NAME:
                payload = await knowledge_delete_tool.handle(runtime.knowledge, arguments)
            elif name == knowledge_update_tool.TOOL_NAME:
                payload = await knowledge_update_tool.handle(runtime.knowledge, arguments)
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
            elif name == impact_tool.TOOL_NAME:
                payload = await impact_tool.handle(runtime, arguments)
            elif name == learn_tool.TOOL_NAME:
                payload = await learn_tool.handle(runtime, arguments)
            elif name == memory_health_tool.TOOL_NAME:
                payload = await memory_health_tool.handle(runtime, arguments)
            elif name == memory_tool.TOOL_NAME:
                payload = await memory_tool.handle(runtime, arguments)
            else:
                payload = {"status": "error", "code": "invalid", "message": f"unknown tool: {name}"}
        except Exception as exc:  # 工具级兜底，避免 MCP 连接中断
            logger.exception("tool %s failed", name)
            payload = {"status": "error", "code": "invalid", "message": str(exc)}
        return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))]

    return server


class _TolerantStdin:
    """丢弃空行与非法 JSON-RPC 行的 stdin 过滤器。

    官方 SDK 的 stdin_reader 把解析异常当消息送进 session，会直接终止
    会话；Glama/Smithery 扫描器常在请求尾部补空行（探针
    blank-line-kills-stdio 复现），此处静默丢弃噪声行保会话。
    """

    def __init__(self, real: AsyncIterator[str]) -> None:
        self._real = real

    def __aiter__(self) -> AsyncIterator[str]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[str]:
        async for line in self._real:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                types.JSONRPCMessage.model_validate_json(stripped)
            except Exception:
                logger.debug("stdio: drop unparseable line (%d chars)", len(line))
                continue
            yield line


async def serve(session: Any) -> None:
    """stdio 传输；runtime 由 session 在首个请求时按 roots 定锚后构建（随 session.stop 收尾）。"""
    server = build_server(session)
    stdin = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace"))
    async with stdio_server(stdin=_TolerantStdin(stdin)) as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
