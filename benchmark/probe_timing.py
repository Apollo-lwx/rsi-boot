"""定位 recall_board 的耗时分布：单 sample 写入 / 索引 / 召回各花多久。"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import benchmark.recall_board as rb


async def main() -> None:
    samples = rb._locomo_samples()
    sample = samples[0]
    print(f"sample={sample['name']} docs={len(sample['rows'])} questions={len(sample['questions'])}")
    work = rb.DATA / "work" / "probe"
    import shutil
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    from rsi_boot.bootstrap import build_runtime
    runtime = await build_runtime(project_root=work)
    print(f"build_runtime {time.monotonic()-t0:.1f}s")
    rb._install_list_cache(runtime.store)
    t0 = time.monotonic()
    mapping = rb._write_docs(work / ".rsi", sample["rows"])
    print(f"write_docs {time.monotonic()-t0:.1f}s")
    t0 = time.monotonic()
    qs = sample["questions"][:20]
    got = await rb._score_questions(runtime, qs, mapping)
    dt = time.monotonic() - t0
    print(f"20 questions {dt:.1f}s -> per-q {dt/20:.2f}s (x2 recalls)")
    await runtime.close()


asyncio.run(main())
