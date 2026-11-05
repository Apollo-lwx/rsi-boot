"""cProfile 定位单次 recall 的热点。"""
from __future__ import annotations

import asyncio
import cProfile
import pstats
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import benchmark.recall_board as rb


async def main() -> None:
    samples = rb._locomo_samples()
    sample = samples[0]
    work = rb.DATA / "work" / "probe"
    import shutil
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    from rsi_boot.bootstrap import build_runtime
    runtime = await build_runtime(project_root=work)
    mapping = rb._write_docs(work / ".rsi", sample["rows"])
    # 热身一次（建索引）
    await rb._score_questions(runtime, sample["questions"][:2], mapping)
    prof = cProfile.Profile()
    prof.enable()
    await rb._score_questions(runtime, sample["questions"][2:8], mapping)
    prof.disable()
    stats = pstats.Stats(prof)
    stats.sort_stats("cumulative").print_stats(25)
    await runtime.close()


asyncio.run(main())
