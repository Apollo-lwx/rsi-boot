"""Mock Provider：测试与无 API Key 环境的本地冒烟用。

模型名 `mock` 或配置 `model.default: mock` 时启用；返回确定性回显文本，
token 用量按 4 字符/token 粗估，保证成本/日志链路可被端到端验证。
"""

from __future__ import annotations

import time
from typing import List

from .base import ModelResult


class MockProvider:
    name = "mock"

    async def complete(self, model: str, messages: List[dict[str, str]], timeout_s: float) -> ModelResult:
        started = time.monotonic()
        user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        text = (
            f"[mock:{model}] 已接收请求（{len(user_msg)} 字符）。\n"
            f"系统提示词长度 {len(system_msg)} 字符"
            + ("，含注入知识。" if "项目知识" in system_msg else "。")
        )
        prompt_chars = sum(len(m["content"]) for m in messages)
        return ModelResult(
            text=text,
            model=model,
            prompt_tokens=prompt_chars // 4,
            completion_tokens=len(text) // 4,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
