"""预热 LLM 查询分解缓存（benchmark 专用，P1）。

并发把 LoCoMo 全部问题分解进共享缓存（RSI_DECOMPOSE_CACHE，默认
benchmark/.cache/decompose.json），之后 recall_board 跑 RSI_ENABLE=decompose
全部命中缓存：零 token、逐位可复现。幂等：已缓存的问题自动跳过。

用法：
    set -a; . benchmark/.env.local; set +a
    python -X utf8 benchmark/warm_decompose_cache.py            # 全量
    python -X utf8 benchmark/warm_decompose_cache.py 0 800      # 区间切片
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "RSI_DECOMPOSE_CACHE", str(ROOT / "benchmark" / ".cache" / "decompose.json")
)
os.environ.setdefault("RSI_DECOMPOSE_MODEL", "kimi-k3.1-pro-highspeed")

_CONCURRENCY = int(os.environ.get("RSI_WARM_CONCURRENCY", "16"))


def main() -> None:
    from benchmark.recall_board import _locomo_samples
    from rsi_boot.rag import decompose as dc

    start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    stop = int(sys.argv[2]) if len(sys.argv) > 2 else 10**9
    questions = [
        q["question"]
        for sample in _locomo_samples()
        for q in sample["questions"]
    ]
    questions = sorted(set(questions))
    todo = questions[start:stop]
    print(f"questions total={len(questions)} slice=[{start}:{stop}] n={len(todo)}")

    rsi_dir = ROOT  # 缓存路径已由 RSI_DECOMPOSE_CACHE 钉死，rsi_dir 只是兜底
    sem = asyncio.Semaphore(_CONCURRENCY)
    done = [0]
    t0 = time.monotonic()

    async def _one(question: str) -> None:
        async with sem:
            await dc.decompose_async(question, rsi_dir)
            done[0] += 1
            if done[0] % 100 == 0:
                rate = done[0] / max(time.monotonic() - t0, 1e-9)
                print(f"{done[0]}/{len(todo)} ({rate:.1f}/s)", flush=True)

    async def _run() -> None:
        await asyncio.gather(*(_one(q) for q in todo))

    asyncio.run(_run())
    elapsed = time.monotonic() - t0
    print(f"done n={len(todo)} elapsed={elapsed:.0f}s cache={dc._cache_path(rsi_dir)}")


if __name__ == "__main__":
    main()
