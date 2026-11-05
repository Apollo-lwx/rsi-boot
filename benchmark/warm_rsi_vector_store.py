"""预热 RSI 向量通道的共享向量库（benchmark 专用）。

把 LoCoMo 各样本的文档文本（title\ncontent，与 VectorIndex._doc_text 同构）
和问题的扩展查询（expand_query 之后，召回实际嵌入的文本）灌进共享 sqlite
（RSI_VECTOR_STORE，默认 benchmark/.cache/rsi-vectors.sqlite），之后的榜单
运行全部命中缓存。幂等：已缓存的文本自动跳过。

用法：
    python -X utf8 benchmark/warm_rsi_vector_store.py            # 全量 10 样本
    python -X utf8 benchmark/warm_rsi_vector_store.py 0 5        # 分片：第 0 片/共 5 片
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("RSI_VECTOR_MODEL", "BAAI/bge-large-en-v1.5")
os.environ.setdefault("RSI_VECTOR_CACHE", str(ROOT / "benchmark" / ".cache" / "fastembed"))
os.environ.setdefault(
    "RSI_VECTOR_STORE", str(ROOT / "benchmark" / ".cache" / "rsi-vectors.sqlite")
)


def main() -> None:
    from benchmark.recall_board import _locomo_samples
    from rsi_boot.rag.query import expand_query
    from rsi_boot.rag.vector import VectorIndex

    shard = None
    if len(sys.argv) >= 3:
        shard = (int(sys.argv[1]), int(sys.argv[2]))

    store_path = Path(os.environ["RSI_VECTOR_STORE"])
    store_path.parent.mkdir(parents=True, exist_ok=True)
    vi = VectorIndex.create(Path(tempfile.mkdtemp()))
    assert vi is not None, "向量通道不可用"

    samples = _locomo_samples()
    if shard is not None:
        i, n = shard
        samples = [s for idx, s in enumerate(samples) if idx % n == i]

    started = time.monotonic()
    for index, sample in enumerate(samples, start=1):
        texts = [f"{t}\n{c}"[:8000] for _gold, t, c in sample["rows"]]
        with tempfile.TemporaryDirectory() as td:
            texts.extend(
                expand_query(q["question"], rsi_dir=Path(td))[0]
                for q in sample["questions"]
            )
        vi._embed(texts)
        print(
            f"sample {index}/{len(samples)}: {len(texts)} texts, "
            f"elapsed {time.monotonic() - started:.0f}s",
            flush=True,
        )
    vi.close()
    print("warm done", flush=True)


if __name__ == "__main__":
    main()
