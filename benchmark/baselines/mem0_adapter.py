"""Mem0 基线适配器（可选依赖）。

没装 mem0ai 或没有 OPENAI_API_KEY 时，open() 抛 RuntimeError，运行器会把它
记为 skipped，而不是让整个对比表跑挂——无 Key 环境仍能出三系统对比。
装上之后：pip install mem0ai，记忆写入走 Memory.add，检索走 Memory.search。
"""

from __future__ import annotations

import os
from pathlib import Path

from benchmark.baselines import BaseAdapter


def _mem0_config() -> dict:
    """mem0 的 LLM/embedding 配置。

    默认（无 OPENAI_BASE_URL）：空 dict，走 mem0 原生默认（OpenAI 官方端点）。
    指向 OpenAI 兼容端点（如 Moonshot/Kimi）时：默认模型名在该端点不存在，
    须显式给出模型——聊天模型 RSI_MEM0_LLM_MODEL（默认 kimi-k2.6），
    向量模型沿用 RSI_BASELINE_EMBED_MODEL（默认 text-embedding-3-small，
    Kimi 端应设 moonshot-v3-embedding）。
    """
    base_url = os.environ.get("OPENAI_BASE_URL")
    if not base_url:
        return {}
    return {
        "llm": {
            "provider": "openai",
            "config": {
                "model": os.environ.get("RSI_MEM0_LLM_MODEL", "kimi-k2.6"),
                "openai_base_url": base_url,
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": os.environ.get(
                    "RSI_BASELINE_EMBED_MODEL", "text-embedding-3-small"
                ),
                "openai_base_url": base_url,
            },
        },
    }


class Mem0Adapter(BaseAdapter):
    name = "mem0"

    def __init__(self) -> None:
        self._memory = None

    def open(self, work: Path) -> None:
        try:
            from mem0 import Memory
        except ImportError as exc:
            raise RuntimeError(
                "mem0ai 未安装：pip install mem0ai 后重跑；本次运行跳过该基线"
            ) from exc
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "mem0 需要 OPENAI_API_KEY（其默认 LLM/embedding 配置）；本次运行跳过该基线"
            )
        self._memory = Memory.from_config(_mem0_config())
        self._user = "bench"

    def reset(self) -> None:
        if self._memory is not None:
            self._memory.delete_all(user_id=self._user)

    def ingest(self, rows: list[tuple[str, str, str]]) -> None:
        assert self._memory is not None
        for gold, title, content in rows:
            # gold_key 放进 metadata，retrieve 时按它投影回评测口径
            self._memory.add(
                f"{title}\n{content}", user_id=self._user, metadata={"gold": gold},
            )

    def retrieve(self, query: str, top_k: int) -> list[str]:
        assert self._memory is not None
        result = self._memory.search(query, user_id=self._user, limit=top_k)
        hits = result.get("results") if isinstance(result, dict) else result
        out: list[str] = []
        for hit in hits or []:
            meta = hit.get("metadata") or {}
            gold = meta.get("gold")
            if gold is not None and gold not in out:
                out.append(str(gold))
        return out

    def notes(self) -> dict:
        cfg = _mem0_config()
        if not cfg:
            return {"pipeline": "mem0 Memory.add/search, default config"}
        return {
            "pipeline": "mem0 Memory.add/search",
            "llm": cfg["llm"]["config"]["model"],
            "embedding": cfg["embedder"]["config"]["model"],
        }
