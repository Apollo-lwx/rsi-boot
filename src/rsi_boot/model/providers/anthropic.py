"""Anthropic Provider（§1.1，P4.1）：Claude 系列模型适配。

与 OpenAI 的接口差异：system 消息走独立参数（不进 messages）、max_tokens 必填、
usage 字段为 input_tokens/output_tokens。SDK 为可选依赖（pip install rsi-boot[anthropic]），
未安装时在构造期给出明确错误。API Key：model.providers.anthropic.api_key 或环境变量
ANTHROPIC_API_KEY。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional

from ...core.exceptions import ModelProviderError, ModelTimeoutError
from .base import ModelResult

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOKENS = 4096


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        if not api_key:
            raise ModelProviderError(
                "缺少 Anthropic API Key（环境变量 ANTHROPIC_API_KEY 或 "
                "~/.rsi/config.yaml model.providers.anthropic.api_key）"
            )
        try:
            from anthropic import AsyncAnthropic  # 延迟导入，可选依赖
        except ImportError as exc:
            raise ModelProviderError(
                "anthropic SDK 未安装：pip install 'rsi-boot[anthropic]'"
            ) from exc
        self._client = AsyncAnthropic(api_key=api_key, base_url=base_url)

    async def complete(self, model: str, messages: List[dict[str, str]], timeout_s: float) -> ModelResult:
        # system 消息提取为独立参数；其余按 Anthropic messages 协议透传
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        chat_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in messages if m["role"] in ("user", "assistant")
        ]
        if not chat_messages:
            chat_messages = [{"role": "user", "content": ""}]

        started = time.monotonic()
        try:
            resp = await asyncio.wait_for(
                self._client.messages.create(
                    model=model,
                    messages=chat_messages,
                    max_tokens=_DEFAULT_MAX_TOKENS,
                    **({"system": system} if system else {}),
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise ModelTimeoutError(f"模型 {model} 调用超时（{timeout_s}s）") from exc
        except ModelProviderError:
            raise
        except Exception as exc:
            # §5.1 口径：HTTP 4xx（除 429）为请求自身问题，不计入熔断失败计数
            status = getattr(exc, "status_code", None)
            countable = not (isinstance(status, int) and 400 <= status < 500 and status != 429)
            raise ModelProviderError(f"模型 {model} 调用失败: {exc}", countable=countable) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        usage = resp.usage
        return ModelResult(
            text=text,
            model=model,
            prompt_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            latency_ms=latency_ms,
        )
