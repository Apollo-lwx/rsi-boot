"""统一日志配置。

serve（MCP stdio）：stdout 被协议占用，日志必须走 stderr（默认形态）。
CLI 一次性命令：默认 WARNING + stdout——Windows PowerShell 把 stderr 渲染为
红色 NativeCommandError 块，INFO 进度日志观感如同命令失败；--verbose 恢复
INFO + stderr。
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Optional

_CONFIGURED = False


def setup_logging(level: str = "INFO", stream: Optional[Any] = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("rsi_boot")
    root.addHandler(handler)
    root.setLevel(level.upper())
    _CONFIGURED = True


def setup_cli_logging(verbose: bool = False) -> None:
    """CLI 子命令统一入口：默认 WARNING+stdout；verbose 恢复 INFO+stderr"""
    if verbose:
        setup_logging("INFO", stream=sys.stderr)
    else:
        setup_logging("WARNING", stream=sys.stdout)
