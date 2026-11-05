"""预热 vanilla-rag 本地向量磁盘缓存。

把 LoCoMo / LongMemEval-S / EngMem-Bench 三个数据集的语料切块与全部查询
按 VanillaRagAdapter.ingest 的同款切法灌进 sqlite 缓存（benchmark/.cache/
embeddings.sqlite），之后的榜单运行命中缓存、不再受单次进程时限约束。
幂等：中断后重跑自动跳过已缓存文本。

用法：
    python -X utf8 benchmark/warm_embed_cache.py            # 全部数据集
    python -X utf8 benchmark/warm_embed_cache.py locomo     # 只热某一个
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.baselines.vanilla_rag import (  # noqa: E402
    VanillaRagAdapter,
    _chunk_text,
)


def _warm(name: str, samples: list[dict], adapter: VanillaRagAdapter) -> None:
    started = time.monotonic()
    done_texts = 0
    for index, sample in enumerate(samples, start=1):
        texts = []
        for _gold, title, content in sample["rows"]:
            texts.extend(_chunk_text(f"{title}\n{content}"))
        texts.extend(q["question"] for q in sample["questions"])
        adapter._embedder.embed(texts)
        done_texts += len(texts)
        print(
            f"{name} [{index}/{len(samples)}] +{len(texts)} texts "
            f"(total {done_texts}, {time.monotonic() - started:.0f}s)",
            flush=True,
        )


def main() -> None:
    which = sys.argv[1:] or ["locomo", "lme", "engmem"]
    adapter = VanillaRagAdapter()
    adapter.open(Path("benchmark/.cache/warm-work"))
    if adapter._fallback:
        print("当前环境无真向量后端（hashing 下界），无需预热", flush=True)
        return

    if "locomo" in which:
        from benchmark.recall_board import _locomo_samples

        _warm("LoCoMo", _locomo_samples(), adapter)
    if "lme" in which:
        from benchmark.recall_board import _longmemeval_samples

        _warm("LongMemEval-S", _longmemeval_samples(), adapter)
    if "engmem" in which:
        from benchmark.engmem.run import _samples

        _warm("EngMem-Bench", _samples(), adapter)
    print("warm done", flush=True)


if __name__ == "__main__":
    main()
