"""DeepSeek Provider（§1.1，P4.1）：OpenAI 兼容端点，仅切换 base_url 与 Key。

API Key：model.providers.deepseek.api_key 或环境变量 DEEPSEEK_API_KEY。
复用 openai SDK，无新增依赖。
"""

from __future__ import annotations

import logging
from typing import Optional

from ...core.exceptions import ModelProviderError
from .openai import OpenAIProvider

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider(OpenAIProvider):
    name = "deepseek"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        if not api_key:
            raise ModelProviderError(
                "缺少 DeepSeek API Key（环境变量 DEEPSEEK_API_KEY 或 "
                "~/.rsi/config.yaml model.providers.deepseek.api_key）"
            )
        super().__init__(api_key=api_key, base_url=base_url or DEEPSEEK_BASE_URL)
