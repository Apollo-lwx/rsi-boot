"""统一模型调用接口（§1.1）：重试（指数退避 full jitter，默认 3 次，§5.3）+ 成本核算（§6.1）
+ 熔断器（§5.1，P4.2）+ 跨厂商 fallback 链（§5.2 llm_primary 降级，P4.2）。

熔断：每个模型独立熔断器（阈值 5 次连续失败，恢复 60s）；OPEN 时 fail-fast
抛 CircuitOpenError，直接进入 fallback 链。HTTP 4xx 不计入失败（§5.1 口径）。
Fallback：配置 `model.fallback: [modelB, ...]`，主模型重试耗尽或熔断 OPEN 时
依次尝试；结果标记 fallback_used=True。
"""

from __future__ import annotations

import asyncio
import logging
import random
from decimal import Decimal
from typing import Any, List, Optional

from ..common.circuit_breaker import CircuitBreakerRegistry
from ..config.loader import load_model_prices
from ..core.exceptions import CircuitOpenError, ModelProviderError, ModelTimeoutError
from ..core.models import CostInfo
from .providers.base import ModelResult
from .registry import ModelRegistry

logger = logging.getLogger(__name__)

_RETRYABLE = (ModelTimeoutError, ModelProviderError)

# §5.2 llm_primary 熔断参数
_LLM_BREAKER_THRESHOLD = 5
_LLM_BREAKER_RECOVERY_S = 60.0


class ModelAdapter:
    def __init__(self, registry: ModelRegistry, config: dict[str, Any]):
        self._registry = registry
        self._config = config
        self._prices = load_model_prices()
        self._breakers = CircuitBreakerRegistry()
        fb = config.get("model", {}).get("fallback") or []
        self._fallback_chain: List[str] = [str(m) for m in fb]

    async def complete(self, model_name: str, messages: List[dict[str, str]]) -> ModelResult:
        """主模型 → fallback 链依次尝试；全部失败抛 ModelProviderError"""
        chain = [model_name] + [m for m in self._fallback_chain if m != model_name]
        last_exc: Optional[Exception] = None
        for index, name in enumerate(chain):
            breaker = self._breakers.get(
                f"llm:{name}", threshold=_LLM_BREAKER_THRESHOLD, recovery_timeout_s=_LLM_BREAKER_RECOVERY_S
            )
            try:
                breaker.allow_request()
            except CircuitOpenError as exc:
                logger.warning("模型 %s 熔断 OPEN，跳过（fallback 链第 %d 环）", name, index)
                last_exc = exc
                continue
            try:
                result = await self._call_with_retry(name, messages)
            except ModelProviderError as exc:
                breaker.on_failure(countable=exc.countable)
                last_exc = exc
                if index < len(chain) - 1:
                    logger.warning("模型 %s 失败，fallback 到 %s: %s", name, chain[index + 1], exc)
                continue
            breaker.on_success()
            if index > 0:
                result.fallback_used = True
                logger.info("fallback 生效：%s → %s", model_name, name)
            return result
        raise ModelProviderError(f"模型链 {chain} 全部失败: {last_exc}")

    async def _call_with_retry(self, model_name: str, messages: List[dict[str, str]]) -> ModelResult:
        model_cfg = self._config.get("model", {})
        timeout_s = float(model_cfg.get("timeout_s", 60))
        max_retries = int(model_cfg.get("max_retries", 3))  # §5.3：LLM 最大重试 3 次

        provider = self._registry.get_provider(model_name)
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                return await provider.complete(model_name, messages, timeout_s)
            except _RETRYABLE as exc:
                last_exc = exc
                if attempt < max_retries:
                    # §5.3 full jitter（防重试风暴同步）：base=1s，cap=30s
                    backoff = random.uniform(0, min(30.0, 2.0 ** attempt))
                    logger.warning("模型调用失败（第 %d 次），%.1fs 后重试: %s", attempt + 1, backoff, exc)
                    await asyncio.sleep(backoff)
        raise ModelProviderError(
            f"模型 {model_name} 重试 {max_retries} 次后仍失败: {last_exc}",
            countable=getattr(last_exc, "countable", True),
        )

    def breaker_states(self) -> dict[str, str]:
        """各模型熔断器状态快照（§5.4 降级级别评估依据）"""
        return self._breakers.states()

    def calc_cost(self, result: ModelResult) -> CostInfo:
        """§6.1：cost = prompt/1e6×单价 + completion/1e6×单价；未定价模型为 None 并告警"""
        price = self._prices.get(result.model)
        cost_usd: Optional[Decimal] = None
        if price is not None:
            cost_usd = Decimal(
                result.prompt_tokens * price.get("prompt", 0.0) / 1_000_000
                + result.completion_tokens * price.get("completion", 0.0) / 1_000_000
            ).quantize(Decimal("0.000001"))
        else:
            logger.warning("模型 %s 未在 models.yaml 定价，cost_usd 记 NULL", result.model)

        return CostInfo(
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            cost_usd=cost_usd if cost_usd is not None else Decimal("0"),
            latency_ms=result.latency_ms,
        )

    def is_priced(self, model_name: str) -> bool:
        return model_name in self._prices
