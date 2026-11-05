"""EngMem-Bench 跑榜：工程记忆（规范/禁令/修复）检索评测。

    python -X utf8 benchmark/engmem/run.py --limit=2                 # 冒烟
    python -X utf8 benchmark/engmem/run.py --systems=rsi --shard=0/4 # 分片
    python -X utf8 benchmark/engmem/run.py --merge                   # 合并出表

每个仓库是一个 eval 单元：语料条目作为 rows 注入，题目按 gold 锚点评
Hit@5/Recall@5/MRR（与 recall_board 同口径），再按 type
（convention/prohibition/fix）与 difficulty（easy/medium/hard）分组出表。
适配器复用 benchmark/run_baselines.py 的 SYSTEMS。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from benchmark.baselines.vanilla_rag import embed_note  # noqa: E402
from benchmark.recall_board import _hit, _pack, _recall, _rr, suppress_event_log  # noqa: E402
from benchmark.run_baselines import SYSTEMS, _load  # noqa: E402

BENCH = Path(__file__).resolve().parent / "bench.yaml"
OUT = ROOT / "benchmark" / "results" / "engmem-dev"
DATASET = "EngMem"

TYPES = ("convention", "prohibition", "fix")
DIFFICULTIES = ("easy", "medium", "hard")


def _samples() -> list[dict]:
    data = yaml.safe_load(BENCH.read_text(encoding="utf-8"))
    samples = []
    for repo in data["repos"]:
        rows = [
            (entry["id"], entry.get("title") or entry["id"], entry.get("content") or "")
            for entry in repo["corpus"]
        ]
        samples.append({
            "name": repo["name"],
            "commit": repo["commit"],
            "rows": rows,
            "questions": repo["questions"],
        })
    return samples


def _score(adapter, questions: list[dict]) -> list[dict]:
    rows = []
    for question in questions:
        gold = list(dict.fromkeys(question["gold"]))
        top5 = adapter.retrieve(question["question"], 5)
        top10 = adapter.retrieve(question["question"], 10)
        rows.append({
            "id": question["id"],
            "qtype": question["type"],
            "difficulty": question.get("difficulty") or "medium",
            "gold": gold,
            "top5": top5,
            "top10": top10,
            "hit5": _hit(top5, gold, 5),
            "recall5": _recall(top5, gold, 5),
            "mrr": _rr(top5, gold),
            "hit10": _hit(top10, gold, 10),
            "recall10": _recall(top10, gold, 10),
            "empty5": not top5,
        })
    return rows


def _run_system(adapter, samples: list[dict], sink, tag: str) -> list[dict]:
    work = ROOT / "benchmark" / ".data" / "work" / f"engmem-{adapter.name}{tag}"
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
                    row["repo"] = sample["name"]
                    sink.write(json.dumps(row, ensure_ascii=False) + "\n")
                sink.flush()
                rows.extend(got)
                packed = _pack(rows)
                print(
                    f"{DATASET}/{adapter.name} {index}/{len(samples)} {sample['name']} "
                    f"hit@5={packed['hit@5']} elapsed={time.monotonic() - started:.0f}s",
                    flush=True,
                )
    finally:
        adapter.close()
    return rows


def _grouped(rows: list[dict]) -> dict:
    """overall + 按题型 + 按难度的分组指标。"""
    out: dict = {"overall": _pack(rows)}
    for qtype in TYPES:
        sub = [r for r in rows if r["qtype"] == qtype]
        if sub:
            out[f"type:{qtype}"] = _pack(sub)
    for level in DIFFICULTIES:
        sub = [r for r in rows if r["difficulty"] == level]
        if sub:
            out[f"difficulty:{level}"] = _pack(sub)
    return out


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _table(title: str, groups: dict[str, dict], keys: list[str]) -> list[str]:
    lines = [f"### {title}", ""]
    lines.append("| 系统 | 题数 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for name, g in groups.items():
        for key in keys:
            packed = g.get(key)
            if not packed:
                continue
            label = name if key == keys[0] else ""
            lines.append(
                f"| {label} | {packed['scored']} | {_fmt(packed['hit@5'])} | "
                f"{_fmt(packed['recall@5'])} | {_num(packed['mrr'])} | "
                f"{_fmt(packed['hit@10'])} | {_fmt(packed['recall@10'])} |"
            )
    lines.append("")
    return lines


def _write_summary(payload: dict) -> None:
    run_date = OUT.name.replace("engmem-", "")
    systems = payload["systems"]
    lines = [
        f"# EngMem-Bench 榜 · {run_date}",
        "",
        "工程记忆检索榜：每仓库一个 eval 单元，题目锚定真实仓库文件节"
        "（构建期解析质检）。指标与召回主榜同口径（检索命中而非模型答题分）。"
        "vanilla-rag 无 OPENAI_API_KEY 时为 hashing 词袋下界；"
        "full-context 是全量塞上下文参照，不参与胜负判定。",
        "",
        "## 总体",
        "",
    ]
    lines += _table("全部题目", systems, ["overall"])
    lines.append("## 按题型")
    lines.append("")
    lines += _table("convention / prohibition / fix", systems,
                    ["type:convention", "type:prohibition", "type:fix"])
    lines.append("## 按难度")
    lines.append("")
    lines += _table("easy / medium / hard", systems,
                    ["difficulty:easy", "difficulty:medium", "difficulty:hard"])
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
    payload = {"systems": {}, "notes": {}}
    for name in systems:
        rows: list[dict] = []
        for path in sorted(OUT.glob(f"{DATASET}.{name}.shard*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        if rows:
            payload["systems"][name] = _grouped(rows)
    _fill_notes(payload)
    _write_summary(payload)
    print(f"merged -> {OUT / 'SUMMARY.md'}", flush=True)


def _fill_notes(payload: dict) -> None:
    present = set(payload["systems"])
    if "rsi" in present:
        payload["notes"]["rsi"] = {"pipeline": "RecallService recall-balanced, cold start"}
    if "vanilla-rag" in present:
        payload["notes"]["vanilla-rag"] = {
            "chunk": "512 chars, overlap 64",
            "embedding": embed_note(),
        }
    if "full-context" in present:
        payload["notes"]["full-context"] = {"role": "upper-bound reference, no retrieval"}


def main() -> None:
    global OUT
    args = sys.argv[1:]
    limit = None
    shard = None
    merge = False
    systems = [s for s in SYSTEMS if s != "mem0"]  # engmem 默认不含 mem0
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
            print(f"unknown arg: {arg}", flush=True)
            sys.exit(2)

    unknown = [s for s in systems if s not in SYSTEMS]
    if unknown:
        print(f"unknown systems: {unknown}; choices: {list(SYSTEMS)}", flush=True)
        sys.exit(2)

    if merge:
        _merge_shards(systems)
        return

    OUT.mkdir(parents=True, exist_ok=True)
    samples = _samples()
    if shard is not None:
        i, n = shard
        samples = [s for idx, s in enumerate(samples) if idx % n == i]
    if limit:
        samples = samples[:limit]
    print(f"{DATASET} repos={len(samples)} systems={systems}", flush=True)

    payload = {"systems": {}, "notes": {}}
    tag = f"-s{shard[0]}of{shard[1]}" if shard else ""
    suffix = f".shard{shard[0]}" if shard else ""
    for name in systems:
        adapter = _load(SYSTEMS[name])
        sink_name = f"{DATASET}.{name}{suffix}.jsonl"
        try:
            with (OUT / sink_name).open("w", encoding="utf-8") as sink:
                rows = _run_system(adapter, samples, sink, tag)
            payload["systems"][name] = _grouped(rows)
            payload["notes"][name] = adapter.notes()
        except RuntimeError as exc:
            print(f"{DATASET}/{name} SKIPPED: {exc}", flush=True)
        _write_summary(payload)
    print(f"done -> {OUT / 'SUMMARY.md'}", flush=True)


if __name__ == "__main__":
    main()
