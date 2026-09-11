"""模型 Provider 协议（§1.1 模型适配层）"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Protocol


@dataclass
class ModelResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    fallback_used: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ModelProvider(Protocol):
    """各厂商适配协议：chat 补全进，统一 ModelResult 出"""

    name: str

    async def complete(self, model: str, messages: List[dict[str, str]], timeout_s: float) -> ModelResult:
        ...
