"""召回主榜：LoCoMo 与 LongMemEval-S 上的 Hit@5 / Recall@5 / MRR，另报 @10。

切分写死在本文件：
- LoCoMo：一条官方 turn（dia_id）一条记忆。正文前加上该 session 的日期。
  类别 1–4 用 evidence 里的 dia_id 做标准证据。类别 5 的答案是空，evidence 是干扰项，
  只计入「该空时空了」，不计入 Hit。
- LongMemEval-S cleaned：一条 haystack session 一条记忆。超过 18000 字再切开，
  同一 session 的切片都算这条证据。标准证据是 answer_session_ids。

走产品召回（RecallService，冷启动 recall-balanced，默认 5 条）。token 不估算。

    python -X utf8 benchmark/recall_board.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi_boot.bootstrap import build_runtime
from rsi_boot.memory import logstore
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc

# 冷启动没有反馈。计分若逐条落日志，下一次召回会把整份日志重读一遍，题量上去之后时间按平方涨。
logstore.append_event = lambda *args, **kwargs: None  # type: ignore[assignment]

DATA = ROOT / "benchmark" / ".data"
OUT = ROOT / "benchmark" / "results" / "recall-20261028"
LOCOMO_CAT = {
    1: "single-hop",
    2: "multi-hop",
    3: "temporal",
    4: "open-domain",
    5: "adversarial",
}
CHUNK = 18000


def _hid(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _chunks(text: str) -> list[str]:
    if len(text) <= 20000:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        parts.append(text[start:start + CHUNK])
        if start + CHUNK >= len(text):
            break
        start += CHUNK
    return parts


def _install_list_cache(store: MemoryStore) -> None:
    """同一批记忆上的召回反复读盘。缓存只在写入后失效，排序仍是产品召回。"""
    original = store.list_official
    store._bench_gen = 0  # type: ignore[attr-defined]
    store._bench_cache: dict = {}  # type: ignore[attr-defined]

    def cached(type: str | None = None, *, skip_harvest: bool = False):
        gen = store._bench_gen  # type: ignore[attr-defined]
        if store._bench_cache.get("_gen") != gen:  # type: ignore[attr-defined]
            store._bench_cache.clear()  # type: ignore[attr-defined]
            store._bench_cache["_gen"] = gen  # type: ignore[attr-defined]
        key = (type, skip_harvest)
        hit = store._bench_cache.get(key)  # type: ignore[attr-defined]
        if hit is None:
            hit = original(type, skip_harvest=skip_harvest)
            store._bench_cache[key] = hit  # type: ignore[attr-defined]
        return hit

    store.list_official = cached  # type: ignore[method-assign]


def _write_docs(rsi_dir: Path, rows: list[tuple[str, str, str]]) -> dict[str, str]:
    """rows = (gold_key, title, content)。返回 doc_id -> gold_key。"""
    dest_root = official_dir(rsi_dir, "documentation")
    if dest_root.exists():
        shutil.rmtree(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(rsi_dir)
    doc_to_gold: dict[str, str] = {}
    for gold_key, title, content in rows:
        title = (title or gold_key)[:120]
        for index, piece in enumerate(_chunks(content or "")):
            doc_id = _hid(f"{gold_key}:{index}:{title}")
            doc_to_gold[doc_id] = gold_key
            piece_title = title if index == 0 else f"{title} #{index + 1}"
            doc = MemoryDoc(
                id=doc_id,
                type="documentation",
                title=piece_title[:120],
                content=piece[:20000],
            )
            store.write(doc, dest=dest_root / memory_filename(doc.title, doc.id))
    return doc_to_gold


def _project(ranked: list[str], doc_to_gold: dict[str, str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for doc_id in ranked:
        gold = doc_to_gold.get(doc_id)
        if not gold or gold in seen:
            continue
        seen.add(gold)
        out.append(gold)
    return out


def _hit(ranked: list[str], gold: list[str], k: int) -> float:
    return 1.0 if set(ranked[:k]) & set(gold) else 0.0


def _recall(ranked: list[str], gold: list[str], k: int) -> float:
    keys = set(gold)
    if not keys:
        return 0.0
    return len(set(ranked[:k]) & keys) / len(keys)


def _rr(ranked: list[str], gold: list[str]) -> float:
    keys = set(gold)
    for index, item in enumerate(ranked):
        if item in keys:
            return 1.0 / (index + 1)
    return 0.0


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _pack(rows: list[dict]) -> dict:
    scored = [row for row in rows if row["gold"]]
    empty = [row for row in rows if not row["gold"]]
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in scored:
        by_type[row["qtype"]].append(row)
    per_type = {}
    for name, group in sorted(by_type.items()):
        per_type[name] = {
            "n": len(group),
            "hit@5": _mean([row["hit5"] for row in group]),
            "recall@5": _mean([row["recall5"] for row in group]),
            "mrr": _mean([row["mrr"] for row in group]),
            "hit@10": _mean([row["hit10"] for row in group]),
            "recall@10": _mean([row["recall10"] for row in group]),
        }
    abstain_hit = sum(1 for row in empty if row["empty5"])
    return {
        "questions": len(rows),
        "scored": len(scored),
        "hit@5": _mean([row["hit5"] for row in scored]),
        "recall@5": _mean([row["recall5"] for row in scored]),
        "mrr": _mean([row["mrr"] for row in scored]),
        "hit@10": _mean([row["hit10"] for row in scored]),
        "recall@10": _mean([row["recall10"] for row in scored]),
        "abstain_n": len(empty),
        "abstain_empty@5": abstain_hit,
        "per_type": per_type,
    }


async def _recall_ids(runtime, task: str, top_k: int | None) -> list[str]:
    payload = await runtime.recall.recall(
        task, runtime.project_id, user_id="bench", top_k=top_k,
    )
    return [item["id"] for item in payload.get("items") or []]


async def _score_questions(runtime, questions: list[dict], doc_to_gold: dict[str, str]) -> list[dict]:
    rows = []
    for question in questions:
        gold = list(dict.fromkeys(question["gold"]))
        top5 = _project(await _recall_ids(runtime, question["question"], None), doc_to_gold)
        top10 = _project(await _recall_ids(runtime, question["question"], 10), doc_to_gold)
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


def _locomo_samples() -> list[dict]:
    path = DATA / "locomo10.json"
    samples = json.loads(path.read_text(encoding="utf-8"))
    prepared = []
    for sample in samples:
        conv = sample["conversation"]
        rows = []
        seen: set[str] = set()
        for key, value in conv.items():
            if not key.startswith("session_") or key.endswith("_date_time"):
                continue
            if not isinstance(value, list):
                continue
            date = str(conv.get(f"{key}_date_time") or "")
            for turn in value:
                dia = str(turn.get("dia_id") or "")
                if not dia or dia in seen:
                    continue
                seen.add(dia)
                text = str(turn.get("text") or "")
                caption = turn.get("blip_caption")
                if caption:
                    text = f"{text}\n{caption}".strip()
                speaker = str(turn.get("speaker") or "")
                content = f"[{date}] {speaker}: {text}".strip()
                rows.append((dia, f"{speaker} {dia}", content))
        questions = []
        for index, qa in enumerate(sample["qa"]):
            category = int(qa["category"])
            evidence = [str(item) for item in (qa.get("evidence") or [])]
            gold = [] if category == 5 else evidence
            questions.append({
                "id": f"{sample['sample_id']}:{index}",
                "qtype": LOCOMO_CAT.get(category, str(category)),
                "question": str(qa["question"]),
                "gold": gold,
            })
        prepared.append({
            "name": str(sample["sample_id"]),
            "rows": rows,
            "questions": questions,
        })
    return prepared


def _longmemeval_samples() -> list[dict]:
    path = DATA / "longmemeval_s_cleaned.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("data") or raw.get("rows") or next(iter(raw.values()))
    prepared = []
    for row in raw:
        session_ids = [str(item) for item in row["haystack_session_ids"]]
        sessions = row["haystack_sessions"]
        dates = row.get("haystack_dates") or []
        docs = []
        for index, session in enumerate(sessions):
            sid = session_ids[index]
            date = str(dates[index]) if index < len(dates) else ""
            lines = [f"[{date}] session {sid}"]
            for turn in session:
                role = str(turn.get("role") or "")
                content = str(turn.get("content") or "")
                lines.append(f"{role}: {content}")
            docs.append((sid, f"{date} {sid}"[:120], "\n".join(lines)))
        qtype = str(row.get("question_type") or "unknown")
        prepared.append({
            "name": str(row.get("question_id") or len(prepared)),
            "rows": docs,
            "questions": [{
                "id": str(row.get("question_id") or len(prepared)),
                "qtype": qtype,
                "question": str(row["question"]),
                "gold": [str(item) for item in (row.get("answer_session_ids") or [])],
            }],
        })
    return prepared


async def _run_dataset(name: str, samples: list[dict], sink) -> dict:
    work = DATA / "work" / name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    runtime = await build_runtime(project_root=work)
    _install_list_cache(runtime.store)
    rows: list[dict] = []
    started = time.monotonic()
    try:
        for index, sample in enumerate(samples, start=1):
            mapping = _write_docs(work / ".rsi", sample["rows"])
            runtime.store._bench_gen += 1  # type: ignore[attr-defined]
            runtime.recall._index = None
            runtime.recall._index_key = None
            got = await _score_questions(runtime, sample["questions"], mapping)
            for row in got:
                sink.write(json.dumps(row, ensure_ascii=False) + "\n")
            sink.flush()
            rows.extend(got)
            if True:
                packed = _pack(rows)
                print(
                    f"{name} {index}/{len(samples)} hit@5={packed['hit@5']} "
                    f"scored={packed['scored']} elapsed={time.monotonic() - started:.0f}s",
                    flush=True,
                )
    finally:
        await runtime.close()
    return _pack(rows)


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _num(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}"


def _write_summary(payload: dict) -> None:
    lines = [
        "# 召回主榜 · 2026-10-28",
        "",
        "产品召回，冷启动 `recall-balanced`（默认 5 条）。Hit / Recall 的分母是有标准证据的题数。MRR 是第一条命中的名次倒数，范围 0 到 1。答案为空的题只进「该空时空了」。token 宿主没有报，记 null。原始记录在同目录的 jsonl。",
        "",
        "| 数据集 | 有证据的题 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 | 该空时空了 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, packed in payload["datasets"].items():
        abstain = packed["abstain_n"]
        empty = packed["abstain_empty@5"]
        abstain_cell = "—" if abstain == 0 else f"{empty}/{abstain}"
        lines.append(
            f"| {name} | {packed['scored']} | {_fmt(packed['hit@5'])} | {_fmt(packed['recall@5'])} | "
            f"{_num(packed['mrr'])} | {_fmt(packed['hit@10'])} | {_fmt(packed['recall@10'])} | {abstain_cell} |"
        )
    lines.extend(["", "## 分项", ""])
    for name, packed in payload["datasets"].items():
        lines.append(f"### {name}")
        lines.append("")
        lines.append("| 题型 | 题数 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for qtype, row in packed["per_type"].items():
            lines.append(
                f"| {qtype} | {row['n']} | {_fmt(row['hit@5'])} | {_fmt(row['recall@5'])} | "
                f"{_num(row['mrr'])} | {_fmt(row['hit@10'])} | {_fmt(row['recall@10'])} |"
            )
        lines.append("")
    lines.append(
        "切分：LoCoMo 一条 turn 一条记忆，正文带 session 日期。类别 5 的答案为空，不进 Hit；另外有 4 题没有 evidence，也算进「该空时空了」。"
        "LongMemEval-S cleaned 一条 session 一条记忆，超过 18000 字按片切开，同一 session 的片都算这条证据。"
        "这张表是检索命中，不是模型答题分，不能和 Mem0 主页上的 92.5 / 94.4 放在同一列。"
    )
    lines.append("")
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )


async def _amain(which: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "scorer": "rsi recall, cold-start recall-balanced",
        "k": [5, 10],
        "tokens": None,
        "datasets": {},
    }
    loaders = {
        "LoCoMo": _locomo_samples,
        "LongMemEval-S": _longmemeval_samples,
    }
    for name in which:
        print(f"load {name}", flush=True)
        samples = loaders[name]()
        print(f"{name} samples={len(samples)}", flush=True)
        with (OUT / f"{name}.jsonl").open("w", encoding="utf-8") as sink:
            payload["datasets"][name] = await _run_dataset(name, samples, sink)
        _write_summary(payload)
    _write_summary(payload)


def main() -> None:
    which = sys.argv[1:] or ["LoCoMo", "LongMemEval-S"]
    asyncio.run(_amain(which))


if __name__ == "__main__":
    main()
