"""Vanilla RAG 基线：512 字符 chunk（64 重叠）+ 向量余弦。

embedding 三档：
- RSI_BASELINE_EMBED_MODEL 含 '/'（如 BAAI/bge-m3）：本地 fastembed/ONNX 向量，
  无需 API Key，磁盘缓存跨分片复用；
- 否则有 OPENAI_API_KEY：OpenAI 兼容 API 向量（批式请求，带缓存），
  默认 text-embedding-3-small，可配合 OPENAI_BASE_URL 接 Kimi 等端点；
- 都没有：确定性 hashing 词袋向量（稀疏 dict，dim=8192），结果里标注 fallback，
  代表「无向量条件下的下界」，论文表格须与真向量列分开标注。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
from array import array
from pathlib import Path

from benchmark.baselines import BaseAdapter

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64
HASH_DIM = 8192
_EMBED_BATCH = 64


def _embed_model() -> str:
    """embedding 模型名：默认 text-embedding-3-small；由 RSI_BASELINE_EMBED_MODEL
    覆盖——含 '/' 走本地 fastembed（如 BAAI/bge-m3），否则走 OpenAI 兼容端点
    （配合 OPENAI_BASE_URL，如 Kimi 的 moonshot-v3-embedding）。"""
    return os.environ.get("RSI_BASELINE_EMBED_MODEL", "text-embedding-3-small")


def embed_note() -> str:
    """榜单注记用：当前环境下 vanilla-rag 实际会用的 embedding 档位。"""
    model = _embed_model()
    if _is_local_model(model) or os.environ.get("OPENAI_API_KEY"):
        return model
    return "hashing-bow (fallback, no embedding backend)"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]")

# 向量 = 稀疏 dict（hashing 档）或 dense list（OpenAI 档）
Vector = dict[int, float] | list[float]


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _chunk_text(text: str) -> list[str]:
    if len(text) <= CHUNK_SIZE:
        return [text]
    out: list[str] = []
    start = 0
    while start < len(text):
        out.append(text[start:start + CHUNK_SIZE])
        if start + CHUNK_SIZE >= len(text):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return out


def _hash_vector(text: str) -> dict[int, float]:
    vec: dict[int, float] = {}
    for token in _tokenize(text):
        h = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
        key = h % HASH_DIM
        vec[key] = vec.get(key, 0.0) + 1.0
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / norm for k, v in vec.items()}


def _cosine(a: Vector, b: Vector) -> float:
    if isinstance(a, dict) and isinstance(b, dict):
        small, big = (a, b) if len(a) <= len(b) else (b, a)
        return sum(v * big.get(k, 0.0) for k, v in small.items())
    return sum(x * y for x, y in zip(a, b))  # type: ignore[arg-type]


class _OpenAIEmbedder:
    def __init__(self) -> None:
        from openai import OpenAI  # 延迟导入：无 Key 环境不必装 openai

        self._client = OpenAI()
        self._cache: dict[str, list[float]] = {}

    def embed(self, texts: list[str]) -> list[Vector]:
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        for start in range(0, len(missing), _EMBED_BATCH):
            batch = missing[start:start + _EMBED_BATCH]
            resp = self._client.embeddings.create(model=_embed_model(), input=batch)
            for text, item in zip(batch, resp.data):
                self._cache[text] = list(item.embedding)
        return [self._cache[t] for t in texts]


class _HashEmbedder:
    def embed(self, texts: list[str]) -> list[Vector]:
        return [_hash_vector(t) for t in texts]


_EMBED_CACHE_DB = Path("benchmark/.cache/embeddings.sqlite")


def _is_local_model(name: str) -> bool:
    """模型名含 '/'（如 BAAI/bge-m3）视为本地 fastembed 模型，否则走 OpenAI 兼容 API。"""
    return "/" in name


class _FastEmbedder:
    """本地开源向量（fastembed/ONNX，无需 API Key），sqlite 磁盘缓存跨分片复用。

    模型文件缓存在 benchmark/.cache/fastembed（HF 布局）；构造时临时设
    HF_HUB_OFFLINE=1，模型已落盘则零网络，未落盘才走正常下载。
    """

    _CACHE_DIR = Path("benchmark/.cache/fastembed")

    def __init__(self, model: str) -> None:
        self._model = model
        self._CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_hf = self._CACHE_DIR / f"models--{model.replace('/', '--')}"
        cache_gcs = self._CACHE_DIR / model.split("/")[-1]
        prev = os.environ.get("HF_HUB_OFFLINE")
        offline_set = cache_hf.is_dir() or (
            cache_gcs.is_dir() and any(cache_gcs.iterdir())
        )
        if offline_set:
            # 必须在导入 fastembed/huggingface_hub 之前设：离线常量是导入期冻结的
            os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            from fastembed import TextEmbedding  # 延迟导入：无依赖环境不受影响

            self._emb = TextEmbedding(
                model_name=model, cache_dir=self._CACHE_DIR, threads=8
            )
        finally:
            if offline_set:
                if prev is None:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                else:
                    os.environ["HF_HUB_OFFLINE"] = prev
        self._mem: dict[str, list[float]] = {}
        _EMBED_CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(_EMBED_CACHE_DB)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS vec (model TEXT, sha TEXT, blob BLOB, "
            "PRIMARY KEY (model, sha))"
        )

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def embed(self, texts: list[str]) -> list[Vector]:
        missing = [t for t in dict.fromkeys(texts) if t not in self._mem]
        # 先查磁盘缓存
        for start in range(0, len(missing), 512):
            chunk = missing[start:start + 512]
            marks = ",".join("?" * len(chunk))
            rows = self._db.execute(
                f"SELECT sha, blob FROM vec WHERE model=? AND sha IN ({marks})",
                [self._model, *[self._key(t) for t in chunk]],
            ).fetchall()
            by_sha = {self._key(t): t for t in chunk}
            for sha, blob in rows:
                self._mem[by_sha[sha]] = list(array("f", blob))
        todo = [t for t in missing if t not in self._mem]
        for start in range(0, len(todo), _EMBED_BATCH):
            batch = todo[start:start + _EMBED_BATCH]
            for text, vec in zip(batch, self._emb.embed(batch)):
                vals = [float(x) for x in vec]
                self._mem[text] = vals
                self._db.execute(
                    "INSERT OR REPLACE INTO vec VALUES (?,?,?)",
                    (self._model, self._key(text), array("f", vals).tobytes()),
                )
            self._db.commit()
        return [self._mem[t] for t in texts]


class VanillaRagAdapter(BaseAdapter):
    name = "vanilla-rag"

    def __init__(self, *, force_hashing: bool = False) -> None:
        self._force_hashing = force_hashing
        self._embedder = None
        self._chunks: list[tuple[str, Vector]] = []  # (gold_key, vector)
        self._fallback = True

    def open(self, work: Path) -> None:
        model = _embed_model()
        if not self._force_hashing and _is_local_model(model):
            try:
                self._embedder = _FastEmbedder(model)
                self._fallback = False
            except Exception:
                self._embedder = _HashEmbedder()
                self._fallback = True
        elif not self._force_hashing and os.environ.get("OPENAI_API_KEY"):
            try:
                self._embedder = _OpenAIEmbedder()
                self._fallback = False
            except Exception:
                self._embedder = _HashEmbedder()
                self._fallback = True
        else:
            self._embedder = _HashEmbedder()
            self._fallback = True

    def reset(self) -> None:
        self._chunks = []

    def ingest(self, rows: list[tuple[str, str, str]]) -> None:
        assert self._embedder is not None
        pieces: list[tuple[str, str]] = []  # (gold_key, chunk_text)
        for gold_key, title, content in rows:
            for piece in _chunk_text(f"{title}\n{content}"):
                pieces.append((gold_key, piece))
        vectors = self._embedder.embed([p for _, p in pieces])
        self._chunks = list(zip([g for g, _ in pieces], vectors))

    def retrieve(self, query: str, top_k: int) -> list[str]:
        assert self._embedder is not None
        if not self._chunks:
            return []
        qvec = self._embedder.embed([query])[0]
        scored = sorted(
            ((gold, _cosine(qvec, vec)) for gold, vec in self._chunks),
            key=lambda item: item[1],
            reverse=True,
        )
        out: list[str] = []
        seen: set[str] = set()
        for gold, _score in scored:
            if gold not in seen:
                seen.add(gold)
                out.append(gold)
                if len(out) >= top_k:
                    break
        return out

    def notes(self) -> dict:
        if self._fallback:
            emb = "hashing-bow (fallback, no embedding backend)"
        else:
            emb = _embed_model()
        return {
            "chunk": f"{CHUNK_SIZE} chars, overlap {CHUNK_OVERLAP}",
            "embedding": emb,
        }
