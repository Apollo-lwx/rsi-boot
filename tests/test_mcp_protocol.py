"""MCP 协议兼容性测试（§9.4 E2E：子进程启动 MCP Server，stdio 直连）。

覆盖：initialize 握手（版本/能力声明）、tools/list 与 schemas/*.json 一致性、
未知工具错误、大消息边界、rsi_recall→rsi_feedback 协议闭环（v3.0 起 rsi_query 退役）。

注意：mcp stdio_client 的 anyio cancel scope 要求 enter/exit 在同一任务，
故不用 async fixture，改为 asynccontextmanager 在测试体内使用。
"""

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


@asynccontextmanager
async def mcp_session(tmp_path):
    """启动子进程 server（RSI_HOME 隔离到临时目录，mock 模型），yield (session, init_result)"""
    rsi_home = tmp_path / ".rsi"
    rsi_home.mkdir()
    (tmp_path / "rsi-boot.yaml").write_text("model:\n  default: mock\n", encoding="utf-8")
    env = {**os.environ, "RSI_HOME": str(rsi_home)}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", "-m", "rsi_boot", "serve"],
        cwd=str(tmp_path),
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            yield session, init_result


async def test_initialize_handshake(tmp_path):
    async with mcp_session(tmp_path) as (session, init_result):
        assert init_result.serverInfo.name == "rsi-boot"
        assert init_result.serverInfo.version
        assert init_result.capabilities.tools is not None
        assert init_result.instructions


async def test_tools_list(tmp_path):
    async with mcp_session(tmp_path) as (session, _):
        tools = (await session.list_tools()).tools
        names = {t.name for t in tools}
        # v3.0 工具族（§6）：rsi_query 退役，rsi_recall / rsi_conflicts 新增
        assert names == {
            "rsi_recall", "rsi_feedback",
            "rsi_knowledge_add", "rsi_knowledge_search", "rsi_knowledge_delete",
            "rsi_knowledge_review",
            "rsi_review", "rsi_conflicts", "rsi_stats",
            "rsi_learn", "rsi_memory",
        }
        for tool in tools:
            assert tool.inputSchema["type"] == "object"
            assert tool.description


async def test_tools_schema_consistent_with_generated_schemas(tmp_path):
    """tools/list 的 inputSchema 字段须与 schemas/*.json（Pydantic 生成）保持一致（§7）"""
    async with mcp_session(tmp_path) as (session, _):
        tools = {t.name: t for t in (await session.list_tools()).tools}

    recall_schema = json.loads((SCHEMAS_DIR / "rsi_recall.schema.json").read_text(encoding="utf-8"))
    tool_props = set(tools["rsi_recall"].inputSchema["properties"])
    assert tool_props <= set(recall_schema["properties"])
    assert set(tools["rsi_recall"].inputSchema["required"]) == {"task"}

    feedback_schema = json.loads((SCHEMAS_DIR / "rsi_feedback.schema.json").read_text(encoding="utf-8"))
    fb_props = set(tools["rsi_feedback"].inputSchema["properties"])
    assert fb_props <= set(feedback_schema["properties"])
    assert set(tools["rsi_feedback"].inputSchema["required"]) == {"feedback_token", "action"}


async def test_unknown_tool_returns_error_payload(tmp_path):
    async with mcp_session(tmp_path) as (session, _):
        result = await session.call_tool("no_such_tool", {})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "error"


async def test_large_input_near_limit(tmp_path):
    async with mcp_session(tmp_path) as (session, _):
        result = await session.call_tool("rsi_recall", {"task": "审" * 8000})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "success"


async def test_recall_feedback_protocol_loop(tmp_path):
    async with mcp_session(tmp_path) as (session, _):
        result = await session.call_tool("rsi_recall", {"task": "帮我审查这段代码"})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "success"
        assert payload["data"]["feedback_token"]

        fb = await session.call_tool(
            "rsi_feedback",
            {"feedback_token": payload["data"]["feedback_token"], "action": "modified", "rating": 3},
        )
        assert json.loads(fb.content[0].text)["status"] == "ok"

        bad = await session.call_tool("rsi_feedback", {"feedback_token": "forged-token", "action": "accepted"})
        assert json.loads(bad.content[0].text)["status"] == "error"
