"""规模可扩展性评测：合成 1k/5k/10k/50k 条记忆，测索引构建与检索延迟。

    python -X utf8 benchmark/scalability.py --sizes=1000,5000 --out=scalability-20261103

合成语料：固定词表随机组合（种子固定，可复现），每条 200-600 字符，
类型在 convention/prohibition/pattern 间轮转。指标：
- 索引构建墙钟（rag.index.build_index）
- 固定 50 条查询的 P50/P95 检索延迟（rag.index.search，BM25）
- tracemalloc 峰值内存（构建期）
全程不启动 runtime，直接测索引层——这是检索规模特性的最纯测量。
"""

from __future__ import annotations

import csv
import json
import random
import statistics
import sys
import time
import tracemalloc
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi_boot.memory.types import MemoryDoc  # noqa: E402
from rsi_boot.rag.index import build_index, search  # noqa: E402

OUT = ROOT / "benchmark" / "results" / "scalability-dev"

_WORDS = (
    "deploy migration cache config queue worker retry timeout schema index "
    "token auth secret vault branch merge review approval lint format test "
    "fixture rollback shard replica latency throttle circuit breaker backoff "
    "payment invoice billing ledger audit compliance encryption signing key "
    "docker kubernetes helm chart ingress service mesh canary blue green "
    "postgres redis kafka rabbitmq celery grpc protobuf openapi swagger "
    "pytest coverage mutation snapshot benchmark profile flame graph tracing"
).split()

_TYPES = ("convention", "prohibition", "pattern", "teaching_case")

QUERIES = [
    "how to rollback a failed migration",
    "deployment freeze policy for the quarter",
    "cache invalidation rules for pricing",
    "required approvals before merging to main",
    "secret rotation procedure with vault",
    "retry and backoff policy for workers",
    "canary release checklist",
    "pytest fixture conventions",
    "kafka consumer lag alert threshold",
    "protobuf generated files editing rules",
] * 5  # 50 条固定查询


def _make_docs(n: int, seed: int = 42) -> list[MemoryDoc]:
    rng = random.Random(seed)
    docs = []
    for i in range(n):
        size = rng.randint(40, 120)
        body = " ".join(rng.choice(_WORDS) for _ in range(size))
        title = f"rule {i}: " + " ".join(rng.choice(_WORDS) for _ in range(6))
        docs.append(MemoryDoc(
            id=uuid.uuid5(uuid.NAMESPACE_DNS, f"scale-{seed}-{i}").hex,
            type=_TYPES[i % len(_TYPES)],
            title=title[:120],
            content=body,
        ))
    return docs


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return ordered[idx]


def _run_size(n: int) -> dict:
    docs = _make_docs(n)
    tracemalloc.start()
    started = time.perf_counter()
    index = build_index(docs)
    build_s = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    latencies: list[float] = []
    for query in QUERIES:
        t0 = time.perf_counter()
        search(index, query, top_n=10)
        latencies.append((time.perf_counter() - t0) * 1000.0)

    row = {
        "docs": n,
        "build_s": round(build_s, 3),
        "p50_ms": round(statistics.median(latencies), 3),
        "p95_ms": round(_p95(latencies), 3),
        "peak_mb": round(peak / 1024 / 1024, 1),
        "terms": len(index.get("postings") or {}),
    }
    print(f"n={n} build={row['build_s']}s p50={row['p50_ms']}ms "
          f"p95={row['p95_ms']}ms peak={row['peak_mb']}MB "
          f"terms={row['terms']}", flush=True)
    return row


def main() -> None:
    global OUT
    sizes = [1000, 5000, 10000, 50000]
    for arg in sys.argv[1:]:
        if arg.startswith("--sizes="):
            sizes = [int(s) for s in arg.split("=", 1)[1].split(",") if s.strip()]
        elif arg.startswith("--out="):
            OUT = ROOT / "benchmark" / "results" / arg.split("=", 1)[1]
        else:
            print(f"unknown arg: {arg}", flush=True)
            sys.exit(2)

    OUT.mkdir(parents=True, exist_ok=True)
    rows = [_run_size(n) for n in sizes]

    with (OUT / "scalability.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["docs", "build_s", "p50_ms", "p95_ms", "peak_mb", "terms"],
        )
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        f"# 规模可扩展性 · {OUT.name.replace('scalability-', '')}",
        "",
        "合成记忆（固定词表随机组合，种子 42），直接测索引层（无 runtime）。",
        "",
        "| 记忆条数 | 构建耗时(s) | 检索 P50(ms) | 检索 P95(ms) | 构建峰值内存(MB) | 词项数 |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['docs']} | {row['build_s']} | {row['p50_ms']} | "
            f"{row['p95_ms']} | {row['peak_mb']} | {row['terms']} |"
        )
    lines += [
        "",
        "口径：BM25 倒排索引（rag.index），50 条固定查询；tracemalloc 峰值",
        "只覆盖构建期 Python 分配，不含解释器常驻。",
    ]
    (OUT / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"done -> {OUT / 'SUMMARY.md'}", flush=True)


if __name__ == "__main__":
    main()
