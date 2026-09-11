"""模型注册中心（§1.1）：模型名 → Provider 实例（进程内缓存复用连接）。

路由规则（P4.1 多模型适配）：mock* → Mock；claude* → Anthropic；
deepseek* → DeepSeek（OpenAI 兼容端点）；其余默认 OpenAI 兼容。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from .providers.base import ModelProvider
from .providers.mock import MockProvider
from .providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)


class ModelRegistry:
    def __init__(self, config: dict[str, Any]):
        self._config = config
        self._providers: Dict[str, ModelProvider] = {}

    def _provider_config(self, name: str) -> dict[str, Any]:
        return dict(self._config.get("model", {}).get("providers", {}).get(name) or {})

    def get_provider(self, model_name: str) -> ModelProvider:
        """按模型名解析 Provider（进程内缓存复用连接）"""
        if model_name.startswith("mock"):
            key = "mock"
            if key not in self._providers:
                self._providers[key] = MockProvider()
            return self._providers[key]

        if model_name.startswith("claude"):
            key = "anthropic"
            if key not in self._providers:
                from .providers.anthropic import AnthropicProvider

                cfg = self._provider_config("anthropic")
                self._providers[key] = AnthropicProvider(
                    api_key=cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY"),
                    base_url=cfg.get("base_url"),
                )
            return self._providers[key]

        if model_name.startswith("deepseek"):
            key = "deepseek"
            if key not in self._providers:
                from .providers.deepseek import DeepSeekProvider

                cfg = self._provider_config("deepseek")
                self._providers[key] = DeepSeekProvider(
                    api_key=cfg.get("api_key") or os.environ.get("DEEPSEEK_API_KEY"),
                    base_url=cfg.get("base_url"),
                )
            return self._providers[key]

        key = "openai"
        if key not in self._providers:
            model_cfg = self._config.get("model", {})
            self._providers[key] = OpenAIProvider(
                api_key=model_cfg.get("api_key"),
                base_url=model_cfg.get("base_url"),
            )
        return self._providers[key]
