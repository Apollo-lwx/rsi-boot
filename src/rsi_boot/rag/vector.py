"""向量通道（实验特性，RSI_ENABLE=vector）：fastembed/ONNX 本地语义检索。

设计纪律：
- **默认关**：仅在 rsi-boot.yaml `enable: [vector]` 或 RSI_ENABLE 环境变量打开时启用
- **懒加载**：fastembed 未安装 / 模型未缓存 / 任何异常 → create() 返回 None，
  召回链路静默退回纯词面通道（fail-open，绝不阻塞召回）
- **磁盘缓存**：文档向量按 (model, sha256(text)) 存 <rsi_dir>/.cache/vectors.sqlite，
  记忆内容不变则不重算；查询向量进程内缓存
- **模型**：默认 bge-small-en-v1.5（~130MB 下载，产品友好）；
  基准对比用 RSI_VECTOR_MODEL=BAAI/bge-large-en-v1.5 与 vanilla-rag 臂对齐；
  中文记忆建议 RSI_VECTOR_MODEL=BAAI/bge-m3
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from array import array
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

if TYPE_CHECKING:
    from ..memory.types import MemoryDoc

logger = logging.getLogger(__name__)

ENV_MODEL = "RSI_VECTOR_MODEL"
ENV_CACHE = "RSI_VECTOR_CACHE"
ENV_STORE = "RSI_VECTOR_STORE"  # 向量 sqlite 共享路径（基准跨样本/跨轮复用）；缺省 <rsi_dir>/.cache
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_EMBED_BATCH = 64
_QUERY_MEMO_MAX = 200


def _default_cache_dir() -> Path:
    """模型缓存目录：RSI_VECTOR_CACHE > XDG_CACHE_HOME > ~/.cache/rsi-boot/fastembed"""
    raw = os.environ.get(ENV_CACHE)
    if raw:
        return Path(raw)
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "rsi-boot" / "fastembed"


def _load_model(model: str, cache_dir: Path):
    """加载 fastembed 模型；缓存已落盘时先设 HF_HUB_OFFLINE=1 再导入（离线可用）。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_hf = cache_dir / f"models--{model.replace('/', '--')}"
    cache_gcs = cache_dir / model.split("/")[-1]
    offline = cache_hf.is_dir() or (cache_gcs.is_dir() and any(cache_gcs.iterdir()))
    prev = os.environ.get("HF_HUB_OFFLINE")
    if offline:
        # 必须在导入 fastembed/huggingface_hub 之前设：离线常量是导入期冻结的
        os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        from fastembed import TextEmbedding  # 延迟导入：未装 fastembed 不影响主链路

        return TextEmbedding(model_name=model, cache_dir=cache_dir, threads=8)
    finally:
        if offline:
            if prev is None:
                os.environ.pop("HF_HUB_OFFLINE", None)
            else:
                os.environ["HF_HUB_OFFLINE"] = prev


class VectorIndex:
    """项目记忆的向量索引：文档向量磁盘缓存 + 查询余弦打分。

    不是独立检索器——产出 {doc_id: cosine} 供 RecallService 与词面通道融合。
    """

    def __init__(self, model: str, cache_dir: Path, store_path: Path) -> None:
        self._model = model
        self._emb = _load_model(model, cache_dir)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(store_path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS vec (model TEXT, sha TEXT, blob BLOB, "
            "PRIMARY KEY (model, sha))"
        )
        self._mem: Dict[str, List[float]] = {}  # sha(text) -> vector
        self._query_memo: Dict[str, List[float]] = {}  # query text -> vector
        self._matrix_key: Optional[str] = None  # 文档集合指纹（doc_id:sha 串联）
        self._matrix_ids: List[str] = []
        self._matrix: Optional[np.ndarray] = None  # 单位化文档向量矩阵

    @classmethod
    def create(cls, rsi_dir: Path) -> Optional["VectorIndex"]:
        """fail-open 工厂：任何一步失败都返回 None（调用方退回纯词面召回）"""
        try:
            model = os.environ.get(ENV_MODEL, DEFAULT_MODEL)
            started = time.monotonic()
            store_raw = os.environ.get(ENV_STORE)
            store_path = (
                Path(store_raw) if store_raw else rsi_dir / ".cache" / "vectors.sqlite"
            )
            index = cls(model, _default_cache_dir(), store_path)
            logger.info(
                "向量通道已启用: %s（加载 %.1fs）", model, time.monotonic() - started
            )
            return index
        except Exception as exc:
            logger.warning("向量通道不可用（退回纯词面召回）: %s", exc)
            return None

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _embed(self, texts: List[str]) -> Dict[str, List[float]]:
        """批量嵌入，返回 sha -> vector；内存 → 磁盘 → 模型三级缓存"""
        missing = [t for t in dict.fromkeys(texts) if self._key(t) not in self._mem]
        for start in range(0, len(missing), 512):
            chunk = missing[start : start + 512]
            marks = ",".join("?" * len(chunk))
            rows = self._db.execute(
                f"SELECT sha, blob FROM vec WHERE model=? AND sha IN ({marks})",
                [self._model, *[self._key(t) for t in chunk]],
            ).fetchall()
            by_sha = {self._key(t): t for t in chunk}
            for sha, blob in rows:
                self._mem[sha] = list(array("f", blob))
        todo = [t for t in missing if self._key(t) not in self._mem]
        for start in range(0, len(todo), _EMBED_BATCH):
            batch = todo[start : start + _EMBED_BATCH]
            for text, vec in zip(batch, self._emb.embed(batch)):
                vals = [float(x) for x in vec]
                sha = self._key(text)
                self._mem[sha] = vals
                self._db.execute(
                    "INSERT OR REPLACE INTO vec VALUES (?,?,?)",
                    (self._model, sha, array("f", vals).tobytes()),
                )
            self._db.commit()
        return {t: self._mem[self._key(t)] for t in dict.fromkeys(texts)}

    @staticmethod
    def _doc_text(doc: "MemoryDoc") -> str:
        # bge 系列上限 512 token（≈2000 英文字符），超出部分模型内部也会截掉，
        # 提前截断省 tokenize 时间（LME 单片 18k 字符，提速约 4 倍）
        return f"{doc.title}\n{doc.content}"[:2000]

    # 长文档分窗：注意力随序列长度平方增长，512 token 整段嵌入的算力足够跑
    # 4 个 500 字符窗口（线性代价），均值池化后覆盖相同文本量但快约 4 倍。
    # ≤2000 字符的文档保持单向量（LoCoMo 已发布数字逐位不变）。
    _WIN_CHARS = 500
    _WIN_COUNT = 4
    _WIN_THRESHOLD = 2000

    @classmethod
    def _doc_chunks(cls, text: str) -> List[str]:
        """长文档（>2000 字符）均匀采 4 个 500 字符窗口；否则整段截断单窗。
        入参为未截断的 title+content 全文。"""
        if len(text) <= cls._WIN_THRESHOLD:
            return [text[:2000]]
        span = len(text) - cls._WIN_CHARS
        return [
            text[int(i * span / (cls._WIN_COUNT - 1)):][: cls._WIN_CHARS]
            for i in range(cls._WIN_COUNT)
        ]

    def _doc_matrix(self, docs: List["MemoryDoc"]) -> np.ndarray:
        """单位化文档向量矩阵；按 (doc_id, 文本长度, 前 2000 字符 sha) 指纹缓存，
        文档集不变则零重算。长文档走分窗嵌入 + 均值池化（见 _doc_chunks）"""
        texts = [f"{d.title}\n{d.content}" for d in docs]
        key = hashlib.sha256(
            "\0".join(
                f"{d.id}:{len(t)}:{self._key(t[:2000])}" for d, t in zip(docs, texts)
            ).encode("utf-8")
        ).hexdigest()
        if self._matrix is not None and self._matrix_key == key:
            return self._matrix
        chunk_lists = [self._doc_chunks(t) for t in texts]
        flat = [c for chunks in chunk_lists for c in chunks]
        vecs = self._embed(flat)
        dim = len(vecs[flat[0]]) if flat else 0
        matrix = np.zeros((len(docs), dim), dtype=np.float32)
        for i, chunks in enumerate(chunk_lists):
            stacked = np.array([vecs[c] for c in chunks], dtype=np.float32)
            matrix[i] = stacked.mean(axis=0)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix = matrix / np.where(norms == 0, 1e-12, norms)
        self._matrix = matrix
        self._matrix_key = key
        self._matrix_ids = [d.id for d in docs]
        return matrix

    def query(
        self, docs: List["MemoryDoc"], query: str, *, top_k: int = 20
    ) -> Dict[str, float]:
        """对项目文档打分，返回 {doc_id: cosine}（仅保留 top_k，全量在内部计算）"""
        if not docs or not query.strip():
            return {}
        try:
            qvec = self._query_memo.get(query)
            if qvec is None:
                qvec = self._embed([query])[query]
                if len(self._query_memo) >= _QUERY_MEMO_MAX:
                    self._query_memo.clear()
                self._query_memo[query] = qvec
            matrix = self._doc_matrix(docs)
            q = np.array(qvec, dtype=np.float32)
            q = q / (np.linalg.norm(q) or 1e-12)
            sims = matrix @ q
            top_idx = np.argsort(-sims)[:top_k]
            return {self._matrix_ids[i]: float(sims[i]) for i in top_idx}
        except Exception as exc:
            logger.warning("向量打分失败（本次召回退回纯词面）: %s", exc)
            return {}

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass
