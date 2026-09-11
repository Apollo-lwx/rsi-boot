"""OpenAI 兼容 Provider（§1.1）：覆盖 OpenAI 及所有 OpenAI 兼容端点（DeepSeek 等经 base_url 切换）。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, List, Optional

from ...core.exceptions import ModelProviderError, ModelTimeoutError
from .base import ModelResult

logger = logging.getLogger(__name__)


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        if not api_key:
            raise ModelProviderError("缺少 OpenAI API Key（环境变量 OPENAI_API_KEY 或 ~/.rsi/config.yaml model.api_key）")
        from openai import AsyncOpenAI  # 延迟导入，未使用时不要求依赖

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(self, model: str, messages: List[dict[str, str]], timeout_s: float) -> ModelResult:
        started = time.monotonic()
        try:
            resp = await asyncio.wait_for(
                self._client.chat.completions.create(model=model, messages=messages),
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
        choice = resp.choices[0] if resp.choices else None
        usage = resp.usage
        return ModelResult(
            text=(choice.message.content if choice and choice.message else "") or "",
            model=model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
        )
