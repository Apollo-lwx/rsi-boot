"""嵌入生成（§2.3/§3.2，P2.1）：EmbeddingService 抽象 + OpenAI / mock 实现。

- 知识条目写入与查询共用同一模型（text-embedding-3-small，1536 维），禁止混用
- 生成失败时条目降级为仅 FTS 可检索（记 warning，不阻塞写入）——由调用方处理
- mock 提供确定性伪向量（哈希播种），用于测试与离线环境
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, List, Optional, Tuple

import numpy as np
from cachetools import TTLCache

from ..common.circuit_breaker import CircuitBreaker
from ..core.exceptions import CircuitOpenError
from ..data.vec import EMBEDDING_DIM

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 8000  # text-embedding-3-small 上下文 8191 token，留余量截断


class EmbeddingService:
    """统一嵌入入口：provider = openai | mock | none（禁用，纯 FTS5）

    结果缓存（§2.4，P4.4）：同一模型下嵌入对同文本确定，按 sha256(model+text)
    进程内缓存（默认 1h，embedding.cache_ttl_s 可调，0 关闭）；失败结果不缓存。
    """

    def __init__(self, config: dict[str, Any]):
        emb_cfg = config.get("embedding", {})
        self.model = str(emb_cfg.get("model", "text-embedding-3-small"))
        self.provider = str(emb_cfg.get("provider", "auto")).lower()
        self._client: Any = None
        cache_ttl = float(emb_cfg.get("cache_ttl_s", 3600))
        self._cache: Optional[TTLCache] = (
            TTLCache(maxsize=1000, ttl=cache_ttl) if cache_ttl > 0 else None
        )
        # §5.2：embedding 依赖独立熔断器（阈值 3 / 恢复 30s）；OPEN 时 fail-fast
        # 返回 None（调用方降级纯 FTS5，§5.4 Level 1），避免每次请求慢失败
        self.breaker = CircuitBreaker("embedding", threshold=3, recovery_timeout_s=30.0)
        if self.provider == "auto":
            api_key = config.get("model", {}).get("api_key")
            self.provider = "openai" if api_key else "none"

    def _cache_key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\0{text}".encode("utf-8")).hexdigest()

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def embed(self, texts: List[str], api_key: Optional[str] = None) -> List[Optional[np.ndarray]]:
        """批量嵌入；单条失败返回 None（该条降级为仅 FTS），不抛异常"""
        if self.provider == "none":
            return [None] * len(texts)
        if self.provider == "mock":
            return [_mock_embed(t) for t in texts]

        self._api_key = api_key
        results: List[Optional[np.ndarray]] = [None] * len(texts)
        misses: List[Tuple[int, str, str]] = []
        for i, text in enumerate(texts):
            key = self._cache_key(text)
            hit = self._cache.get(key) if self._cache is not None else None
            if hit is not None:
                results[i] = hit
            else:
                misses.append((i, text, key))
        if not misses:
            return results

        try:
            self.breaker.allow_request()
        except CircuitOpenError:
            logger.warning("embedding 熔断 OPEN，跳过向量通道（降级纯 FTS5）")
            return results
        try:
            client = self._get_client()
            resp = await client.embeddings.create(
                model=self.model,
                input=[t[:MAX_INPUT_CHARS] for _, t, _ in misses],
            )
            got = [np.array(item.embedding, dtype=np.float32) for item in resp.data]
            for j, (i, _, key) in enumerate(misses):
                vec = got[j] if j < len(got) else None  # 防御：返回数不足按 None 补齐
                results[i] = vec
                if vec is not None and self._cache is not None:
                    self._cache[key] = vec
            self.breaker.on_success()
        except Exception as exc:
            # §5.1 口径：HTTP 4xx（除 429）为请求自身问题，不计入熔断失败计数
            status = getattr(exc, "status_code", None)
            countable = not (isinstance(status, int) and 400 <= status < 500 and status != 429)
            self.breaker.on_failure(countable=countable)
            logger.warning("embedding 生成失败（%s），条目降级为仅 FTS 可检索", exc)
        return results

    async def embed_one(self, text: str, api_key: Optional[str] = None) -> Optional[np.ndarray]:
        return (await self.embed([text], api_key=api_key))[0]


def _mock_embed(text: str) -> np.ndarray:
    """确定性伪向量：文本哈希播种 RNG。同义文本不保证相似，仅供链路测试"""
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    return vec / (np.linalg.norm(vec) or 1e-12)


def serialize_embedding(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def deserialize_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
