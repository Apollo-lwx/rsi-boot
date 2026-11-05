"""基线对比榜：同一批题，RSI / Vanilla RAG / Full-context / Mem0 同口径跑分。

    python -X utf8 benchmark/run_baselines.py --limit=2
    python -X utf8 benchmark/run_baselines.py --systems=rsi,vanilla-rag --out=baselines-20261103

指标与 benchmark/recall_board.py 完全同口径（Hit@5/Recall@5/MRR/Hit@10/Recall@10），
数据加载直接复用 recall_board 的切分器。某系统 open() 抛 RuntimeError（缺依赖/缺 Key）
记为 skipped，不影响其它系统出表。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.baselines.vanilla_rag import embed_note  # noqa: E402
from benchmark.recall_board import (  # noqa: E402
    DATA,
    _hit,
    _locomo_samples,
    _longmemeval_samples,
    _pack,
    _recall,
    _rr,
    suppress_event_log,
)

OUT = ROOT / "benchmark" / "results" / "baselines-dev"

SYSTEMS = {
    "rsi": "benchmark.baselines.rsi_adapter:RsiAdapter",
    "vanilla-rag": "benchmark.baselines.vanilla_rag:VanillaRagAdapter",
    "full-context": "benchmark.baselines.full_context:FullContextAdapter",
    "mem0": "benchmark.baselines.mem0_adapter:Mem0Adapter",
}


def _load(spec: str):
    module_name, _, cls_name = spec.partition(":")
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, cls_name)()


def _score(adapter, questions: list[dict]) -> list[dict]:
    rows = []
    for question in questions:
        gold = list(dict.fromkeys(question["gold"]))
        top5 = adapter.retrieve(question["question"], 5)
        top10 = adapter.retrieve(question["question"], 10)
        rows.append({
            "id": question["id"],
            "qtype": question["qtype"],
            "gold": gold,
            "top5": top5,
            "top10": top10,
            "hit5": _hit(top5, gold, 5) if gold else None,
            "recall5": _recall(top5, gold, 5) if gold else None,
            "mrr": _rr(top5, gold) if gold else None,
            "hit10": _hit(top10, gold, 10) if gold else None,
            "recall10": _recall(top10, gold, 10) if gold else None,
            "empty5": not top5,
        })
    return rows


def _run_system(adapter, dataset: str, samples: list[dict], sink, tag: str) -> dict:
    work = DATA / "work" / f"base-{dataset}-{adapter.name}{tag}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    adapter.open(work)
    rows: list[dict] = []
    started = time.monotonic()
    try:
        with suppress_event_log():
            for index, sample in enumerate(samples, start=1):
                adapter.reset()
                adapter.ingest(sample["rows"])
                got = _score(adapter, sample["questions"])
                for row in got:
                    sink.write(json.dumps(row, ensure_ascii=False) + "\n")
                sink.flush()
                rows.extend(got)
                packed = _pack(rows)
                print(
                    f"{dataset}/{adapter.name} {index}/{len(samples)} "
                    f"hit@5={packed['hit@5']} elapsed={time.monotonic() - started:.0f}s",
                    flush=True,
                )
    finally:
        adapter.close()
    return _pack(rows)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _write_summary(payload: dict) -> None:
    run_date = OUT.name.replace("baselines-", "")
    lines = [
        f"# 基线对比榜 · {run_date}",
        "",
        "与召回主榜同口径（Hit@5 等，检索命中而非模型答题分）。"
        "vanilla-rag 的 embedding 档位见「系统注记」：真向量（API 或本地开源模型）"
        "与 hashing 词袋下界不可混列，混用时会按数据集分别标注。"
        "full-context 是「全量塞上下文」上界参照，不参与胜负判定。"
        "skipped 的系统是缺依赖或缺 Key，不是 0 分。",
        "",
    ]
    for dataset, systems in payload["datasets"].items():
        lines.append(f"## {dataset}")
        lines.append("")
        lines.append("| 系统 | 有证据的题 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 | 该空时空了 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for name, packed in systems.items():
            if "skipped" in packed:
                lines.append(f"| {name} | — | skipped（{packed['skipped']}） | — | — | — | — | — |")
                continue
            abstain = packed["abstain_n"]
            empty_cell = "—" if abstain == 0 else f"{packed['abstain_empty@5']}/{abstain}"
            lines.append(
                f"| {name} | {packed['scored']} | {_fmt(packed['hit@5'])} | "
                f"{_fmt(packed['recall@5'])} | {_num(packed['mrr'])} | "
                f"{_fmt(packed['hit@10'])} | {_fmt(packed['recall@10'])} | {empty_cell} |"
            )
        lines.append("")
    notes = payload.get("notes") or {}
    if notes:
        lines.append("## 系统注记")
        lines.append("")
        for name, note in notes.items():
            detail = ", ".join(f"{k}={v}" for k, v in note.items())
            lines.append(f"- {name}: {detail}")
        lines.append("")
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _merge_shards(systems: list[str]) -> None:
    """合并 --shard 产出的 <dataset>.<system>.shard<i>.jsonl 成总榜。"""
    loaders = {"LoCoMo": None, "LongMemEval-S": None}
    payload = {"datasets": {name: {} for name in loaders}, "notes": {}}
    for dataset in loaders:
        for name in systems:
            rows: list[dict] = []
            for path in sorted(OUT.glob(f"{dataset}.{name}.shard*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        rows.append(json.loads(line))
            if rows:
                payload["datasets"][dataset][name] = _pack(rows)
    # 注记在合并时按当前环境重新推导（分片各自的 summary.json 会被覆盖，靠不住）
    present = {s for systems_ in payload["datasets"].values() for s in systems_}
    if "rsi" in present:
        payload["notes"]["rsi"] = {"pipeline": "RecallService recall-balanced, cold start"}
    if "vanilla-rag" in present:
        payload["notes"]["vanilla-rag"] = {
            "chunk": "512 chars, overlap 64",
            "embedding": embed_note(),
        }
    if "full-context" in present:
        payload["notes"]["full-context"] = {"role": "upper-bound reference, no retrieval"}
    # 混合榜（不同系统/数据集用不同 embedding 档）时用 notes.override.json 如实修正
    override = OUT / "notes.override.json"
    if override.is_file():
        for sys_name, note in json.loads(override.read_text(encoding="utf-8")).items():
            payload["notes"].setdefault(sys_name, {}).update(note)
    _write_summary(payload)
    print(f"merged -> {OUT / 'SUMMARY.md'}", flush=True)


def main() -> None:
    global OUT
    args = sys.argv[1:]
    limit = None
    shard = None
    merge = False
    systems = list(SYSTEMS)
    which: list[str] = []
    for arg in args:
        if arg == "--merge":
            merge = True
        elif arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])
        elif arg.startswith("--shard="):
            i, _, n = arg.split("=", 1)[1].partition("/")
            shard = (int(i), int(n))
        elif arg.startswith("--out="):
            OUT = ROOT / "benchmark" / "results" / arg.split("=", 1)[1]
        elif arg.startswith("--systems="):
            systems = [s.strip() for s in arg.split("=", 1)[1].split(",") if s.strip()]
        else:
            which.append(arg)

    unknown = [s for s in systems if s not in SYSTEMS]
    if unknown:
        print(f"unknown systems: {unknown}; choices: {list(SYSTEMS)}", flush=True)
        sys.exit(2)

    if merge:
        _merge_shards(systems)
        return

    OUT.mkdir(parents=True, exist_ok=True)
    loaders = {
        "LoCoMo": _locomo_samples,
        "LongMemEval-S": _longmemeval_samples,
    }
    which = which or list(loaders)
    payload = {"datasets": {}, "notes": {}}
    tag = f"-s{shard[0]}of{shard[1]}" if shard else ""
    suffix = f".shard{shard[0]}" if shard else ""

    for dataset in which:
        samples = loaders[dataset]()
        if shard is not None:
            i, n = shard
            samples = [s for idx, s in enumerate(samples) if idx % n == i]
        if limit:
            samples = samples[:limit]
        print(f"{dataset} samples={len(samples)} systems={systems}", flush=True)
        payload["datasets"][dataset] = {}
        for name in systems:
            adapter = _load(SYSTEMS[name])
            sink_name = f"{dataset}.{name}{suffix}.jsonl"
            try:
                with (OUT / sink_name).open("w", encoding="utf-8") as sink:
                    packed = _run_system(adapter, dataset, samples, sink, tag)
                payload["datasets"][dataset][name] = packed
                payload["notes"][name] = adapter.notes()
            except RuntimeError as exc:
                print(f"{dataset}/{name} SKIPPED: {exc}", flush=True)
                payload["datasets"][dataset][name] = {"skipped": str(exc)}
            _write_summary(payload)
    _write_summary(payload)
    print(f"done -> {OUT / 'SUMMARY.md'}", flush=True)


if __name__ == "__main__":
    main()
