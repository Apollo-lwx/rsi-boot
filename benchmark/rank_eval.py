"""Compare the previous tf×IDF ranker with BM25 on two sets.

- official: tests/eval/datasets retrieval corpus (similar-length keyword docs)
- length_stress: one short titled note versus six long notes that repeat the keyword

RepoMem Bench measures whether an agent edits tests. Its C-arm seeds are 3–4 short
memories, all of which fit in one recall, so that bench cannot see a ranker change.
This script is the measurement for the recall scorer.

    python -X utf8 benchmark/rank_eval.py
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag.index import build_index, search
from rsi_boot.services.recall_service import RecallService

import importlib.util

_HARNESS_PATH = ROOT / "tests" / "eval" / "harness.py"
_spec = importlib.util.spec_from_file_location("eval_harness", _HARNESS_PATH)
assert _spec is not None and _spec.loader is not None
_harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_harness)
DATASETS = _harness.DATASETS
load_jsonl = _harness.load_jsonl
retrieval_metrics = _harness.retrieval_metrics


def _hid(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def _official() -> dict[str, dict]:
    corpus = load_jsonl(DATASETS / "retrieval_corpus.jsonl")
    queries = load_jsonl(DATASETS / "retrieval_queries.jsonl")
    by_type: dict[str, list[MemoryDoc]] = {}
    id_map: dict[str, str] = {}
    for row in corpus:
        doc_id = _hid(row["id"])
        id_map[row["id"]] = doc_id
        by_type.setdefault(row["project_type"], []).append(MemoryDoc(
            id=doc_id,
            type="documentation",
            title=row["title"],
            content=row["content"],
        ))
    indexes = {ptype: build_index(docs) for ptype, docs in by_type.items()}
    out: dict[str, dict] = {}
    for scorer in ("tfidf", "bm25"):
        results = []
        for query in queries:
            index = indexes[query["project_type"]]
            ranked = [
                doc_id for doc_id, _score in search(
                    index, query["query"], scorer=scorer, top_n=5,
                )
            ]
            relevant = [id_map[item] for item in query["relevant"]]
            results.append((ranked, relevant, query["project_type"]))
        out[scorer] = retrieval_metrics(results, k=5)
    return out


def _stress() -> dict[str, dict]:
    docs: list[MemoryDoc] = []
    queries: list[tuple[str, list[str]]] = []
    for i in range(12):
        key = f"kw{i}alpha"
        relevant = _hid(f"rel-{i}")
        docs.append(MemoryDoc(
            id=relevant, type="convention", title=key, content=key,
        ))
        filler = " ".join(f"pad{i}{k}" for k in range(80))
        for j in range(6):
            docs.append(MemoryDoc(
                id=_hid(f"dist-{i}-{j}"),
                type="documentation",
                title=f"notes {i} {j}",
                content=(key + " ") * 40 + filler,
            ))
        queries.append((key, [relevant]))
    index = build_index(docs)
    out: dict[str, dict] = {}
    for scorer in ("tfidf", "bm25"):
        results = []
        for query, relevant in queries:
            ranked = [
                doc_id for doc_id, _score in search(
                    index, query, scorer=scorer, top_n=5, drop_weak=False,
                )
            ]
            results.append((ranked, relevant, "length_stress"))
        out[scorer] = retrieval_metrics(results, k=5)
    return out


async def _cache_timing() -> dict[str, float | int]:
    import tempfile

    from rsi_boot.rag import index as index_mod

    calls = {"n": 0}
    real = index_mod.build_index

    def _wrapped(docs):
        calls["n"] += 1
        return real(docs)

    index_mod.build_index = _wrapped
    try:
        with tempfile.TemporaryDirectory(prefix="rsi-rank-") as tmp:
            store = MemoryStore(Path(tmp) / ".rsi")
            for i in range(200):
                doc = MemoryDoc(
                    id=_hid(f"doc-{i}"),
                    type="convention",
                    title=f"topic {i % 7}",
                    content=f"keyword{i % 7} " + ("detail " * 40) + f"serial {i}",
                )
                store.write(
                    doc,
                    dest=official_dir(store.rsi_dir, doc.type) / memory_filename(doc.title, doc.id),
                )
            service = RecallService(store=store, feedback_secret="rank-eval")
            started = time.perf_counter()
            await service.recall("keyword0 detail", "p")
            cold_ms = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            await service.recall("keyword3 detail", "p")
            warm_ms = (time.perf_counter() - started) * 1000
            return {"build_calls": calls["n"], "cold_ms": cold_ms, "warm_ms": warm_ms}
    finally:
        index_mod.build_index = real


def _line(label: str, metrics: dict) -> str:
    return (
        f"{label:<28} Recall@5 {metrics['recall@5']:.3f}   "
        f"MRR {metrics['mrr']:.3f}   n={metrics['queries']}"
    )


def main() -> None:
    official = _official()
    stress = _stress()
    timing = asyncio.run(_cache_timing())
    print("official retrieval set (keyword docs, similar length)")
    print(_line("tfidf (previous)", official["tfidf"]))
    print(_line("bm25 + title", official["bm25"]))
    print()
    print("length stress (short title vs 6 long repetitions, 12 queries)")
    print(_line("tfidf (previous)", stress["tfidf"]))
    print(_line("bm25 + title", stress["bm25"]))
    print()
    print(
        "index cache, 200 memories, two recalls: "
        f"build_index calls={timing['build_calls']}  "
        f"cold={timing['cold_ms']:.1f}ms  warm={timing['warm_ms']:.1f}ms"
    )


if __name__ == "__main__":
    main()
