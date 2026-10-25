#!/usr/bin/env python3
"""临时工具：通过 stdio MCP 调 rsi-boot 的工具（用于基准 C 组的审批流）。

用法：
  python benchmark/mcp_call.py <workdir> --list                 # 列工具与 schema
  python benchmark/mcp_call.py <workdir> <tool> '<json-args>'   # 调工具
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    workdir = sys.argv[1]
    mode = sys.argv[2]
    env = dict(os.environ)
    env["RSI_PROJECT_ROOT"] = workdir
    params = StdioServerParameters(command="rsi", args=["serve"], cwd=workdir, env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if mode == "--list":
                tools = await session.list_tools()
                for t in tools.tools:
                    print(f"== {t.name} ==")
                    print(json.dumps(t.inputSchema, ensure_ascii=False)[:800])
                return
            tool = mode
            raw = sys.argv[3] if len(sys.argv) > 3 else "{}"
            if raw.startswith("@"):  # PowerShell 会吃掉内联 JSON 的引号，走文件
                raw = open(raw[1:], encoding="utf-8").read()
            args = json.loads(raw)
            result = await session.call_tool(tool, args)
            for block in result.content:
                print(getattr(block, "text", block))


if __name__ == "__main__":
    asyncio.run(main())
