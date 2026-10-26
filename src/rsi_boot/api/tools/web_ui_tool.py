"""rsi_web_ui：在已运行的 rsi serve 进程里开关本机记忆大屏。

不增加启动参数。open 之前没有端口在听，close 之后页面不可访问。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ...ux.lang import locale_lang
from ...ux.messages import TOOL_DESC, t

TOOL_NAME = "rsi_web_ui"
TOOL_DESCRIPTION = TOOL_DESC["web_ui"]

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["open", "close", "status"],
            "description": (
                "open 开启本机页面（默认）；close 关闭后地址不可访问；"
                "status 只查询是否在听。不要改 rsi serve 的启动参数。"
            ),
        },
    },
    "required": [],
}


def _rsi_dir(runtime: Any) -> Path:
    store = getattr(runtime, "store", None)
    rsi_dir = getattr(store, "rsi_dir", None)
    if rsi_dir is not None:
        return Path(rsi_dir)
    root = getattr(runtime, "_project_root", None)
    if root is None:
        raise ValueError("runtime has no project .rsi directory")
    return Path(root) / ".rsi"


async def handle(runtime: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    from ...dashboard.server import close_dashboard, dashboard_status, open_dashboard

    lang = str(arguments.get("lang") or locale_lang())
    if lang not in ("zh", "en"):
        lang = locale_lang()
    action = str(arguments.get("action") or "open")
    if action not in ("open", "close", "status"):
        return {
            "status": "error",
            "code": "invalid",
            "message": t("WEB_UI_BAD_ACTION", lang),
        }
    try:
        rsi_dir = _rsi_dir(runtime)
        if action == "open":
            data = await asyncio.to_thread(open_dashboard, rsi_dir)
            message = t("WEB_UI_OPENED", lang, url=data["url"])
        elif action == "close":
            data = await asyncio.to_thread(close_dashboard)
            message = t("WEB_UI_CLOSED", lang)
        else:
            data = await asyncio.to_thread(dashboard_status)
            if data.get("listening"):
                message = t("WEB_UI_LISTENING", lang, url=data["url"])
            else:
                message = t("WEB_UI_IDLE", lang)
    except Exception as exc:
        return {"status": "error", "code": "web_ui_failed", "message": str(exc)}
    return {"status": "success", "message": message, "data": data}
