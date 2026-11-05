"""旁路探测。不改 recall_board.py，不走产品 RecallService，不覆盖主榜结果。

同一套切分上比三列，前 5 和前 10 都取自同一次排序：
- plain：rag.index.search，带弱词过滤，没有对数门
- stem：问句和正文先做英文轻词干，再同样 search
- dated：stem 的前 40 名里，问句的月份或年份撞上记忆开头方括号里的日期，分数乘 1.25，再截到前 10

    python -X utf8 benchmark/recall_lexical_probe.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag.index import build_index, search

from recall_board import (  # noqa: E402
    _chunks,
    _hid,
    _hit,
    _locomo_samples,
    _longmemeval_samples,
    _pack,
    _project,
    _recall,
    _rr,
)

OUT = ROOT / "benchmark" / "results" / "recall-lexical-20261028"
_WORD = re.compile(r"[A-Za-z]+")
_BRACKET = re.compile(r"^\[([^\]]+)\]")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_MONTHS = {
    "january": "january", "february": "february", "march": "march",
    "april": "april", "may": "may", "june": "june", "july": "july",
    "august": "august", "september": "september", "october": "october",
    "november": "november", "december": "december",
    "jan": "january", "feb": "february", "mar": "march", "apr": "april",
    "jun": "june", "jul": "july", "aug": "august", "sep": "september",
    "sept": "september", "oct": "october", "nov": "november", "dec": "december",
}
ARMS = ("plain", "stem", "dated")


def _stem_token(word: str) -> str:
    token = word.lower()
    if len(token) < 5 or not token.isascii():
        return token
    if token.endswith("ies") and len(token) > 5:
        return token[:-3] + "y"
    if token.endswith("ing") and len(token) > 6:
        return token[:-3]
    if token.endswith("ed") and len(token) > 5:
        return token[:-2]
    if token.endswith("es") and len(token) > 5:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 5:
        return token[:-1]
    return token


def _stem_text(text: str) -> str:
    return _WORD.sub(lambda match: _stem_token(match.group(0)), text)


def _when(text: str) -> tuple[set[str], set[str]]:
    months = set()
    for match in _WORD.finditer(text.lower()):
        canon = _MONTHS.get(match.group(0))
        if canon:
            months.add(canon)
    return months, set(_YEAR.findall(text))


def _docs(rows: list[tuple[str, str, str]], *, stem: bool) -> tuple[list[MemoryDoc], dict[str, str], dict[str, str]]:
    docs: list[MemoryDoc] = []
    doc_to_gold: dict[str, str] = {}
    doc_date: dict[str, str] = {}
    for gold_key, title, content in rows:
        title = (title or gold_key)[:120]
        bracket = _BRACKET.match(content or "")
        date = bracket.group(1) if bracket else ""
        body = _stem_text(content or "") if stem else (content or "")
        shown = _stem_text(title) if stem else title
        for index, piece in enumerate(_chunks(body)):
            doc_id = _hid(f"{gold_key}:{index}:{title}")
            doc_to_gold[doc_id] = gold_key
            doc_date[doc_id] = date
            piece_title = shown if index == 0 else f"{shown} #{index + 1}"
            docs.append(MemoryDoc(
                id=doc_id,
                type="documentation",
                title=piece_title[:120],
                content=piece[:20000],
            ))
    return docs, doc_to_gold, doc_date


def _rank_plain(index: dict, question: str, doc_to_gold: dict[str, str]) -> list[str]:
    ranked = search(
        index, question, types={"documentation"}, top_n=10, drop_weak=True,
    )
    return _project([doc_id for doc_id, _score in ranked], doc_to_gold)


def _rank_dated(
    index: dict,
    question: str,
    doc_to_gold: dict[str, str],
    doc_date: dict[str, str],
) -> list[str]:
    ranked = search(
        index, question, types={"documentation"}, top_n=40, drop_weak=True,
    )
    q_months, q_years = _when(question)
    # “may” 也是情态动词。问句里没有日期数字时，不拿月份去加分。
    if not q_years and not re.search(r"\b\d{1,2}\b", question):
        q_months = set()
    adjusted: list[tuple[str, float]] = []
    for doc_id, score in ranked:
        months, years = _when(doc_date.get(doc_id, ""))
        boost = 1.25 if (q_months & months) or (q_years & years) else 1.0
        adjusted.append((doc_id, score * boost))
    adjusted.sort(key=lambda item: item[1], reverse=True)
    return _project([doc_id for doc_id, _score in adjusted[:10]], doc_to_gold)


def _score_sample(sample: dict) -> list[dict]:
    plain_docs, doc_to_gold, doc_date = _docs(sample["rows"], stem=False)
    stem_docs, _stem_gold, _stem_dates = _docs(sample["rows"], stem=True)
    plain_index = build_index(plain_docs)
    stem_index = build_index(stem_docs)
    rows = []
    for question in sample["questions"]:
        gold = list(dict.fromkeys(question["gold"]))
        text = str(question["question"])
        lists = {
            "plain": _rank_plain(plain_index, text, doc_to_gold),
            "stem": _rank_plain(stem_index, _stem_text(text), doc_to_gold),
            "dated": _rank_dated(stem_index, _stem_text(text), doc_to_gold, doc_date),
        }
        row = {"id": question["id"], "qtype": question["qtype"], "gold": gold}
        for arm, ranked in lists.items():
            row[f"{arm}_top5"] = ranked[:5]
            row[arm] = {
                "hit5": _hit(ranked, gold, 5) if gold else None,
                "recall5": _recall(ranked, gold, 5) if gold else None,
                "mrr": _rr(ranked, gold) if gold else None,
                "hit10": _hit(ranked, gold, 10) if gold else None,
                "recall10": _recall(ranked, gold, 10) if gold else None,
                "empty5": not ranked[:5],
            }
        rows.append(row)
    return rows


def _arm_rows(rows: list[dict], arm: str) -> list[dict]:
    packed = []
    for row in rows:
        cell = row[arm]
        packed.append({
            "qtype": row["qtype"],
            "gold": row["gold"],
            "hit5": cell["hit5"],
            "recall5": cell["recall5"],
            "mrr": cell["mrr"],
            "hit10": cell["hit10"],
            "recall10": cell["recall10"],
            "empty5": cell["empty5"],
        })
    return packed


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _num(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}"


def _write(name: str, rows: list[dict], payload: dict) -> None:
    lines = [
        "# 词干和日期探测 · 2026-10-28",
        "",
        "这不是主榜。主榜仍是 `recall-20261028`，走产品召回。这里直接调用 `search`，没有对数门。前 5 是同一次排序的前 5，不是另一次默认 5 条召回。",
        "",
        "| 数据集 | 列 | 有证据的题 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for dataset, arms in payload["datasets"].items():
        for arm, packed in arms.items():
            lines.append(
                f"| {dataset} | {arm} | {packed['scored']} | {_fmt(packed['hit@5'])} | "
                f"{_fmt(packed['recall@5'])} | {_num(packed['mrr'])} | {_fmt(packed['hit@10'])} | "
                f"{_fmt(packed['recall@10'])} |"
            )
    lines.extend(["", "## 分项", ""])
    for dataset, arms in payload["datasets"].items():
        lines.append(f"### {dataset}")
        lines.append("")
        lines.append("| 列 | 题型 | 题数 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
        for arm, packed in arms.items():
            for qtype, row in packed["per_type"].items():
                lines.append(
                    f"| {arm} | {qtype} | {row['n']} | {_fmt(row['hit@5'])} | {_fmt(row['recall@5'])} | "
                    f"{_num(row['mrr'])} | {_fmt(row['hit@10'])} | {_fmt(row['recall@10'])} |"
                )
        lines.append("")
    lines.append("plain 是原文 BM25。stem 把英文词尾 ing/ed/es/s 收一下。dated 在 stem 上，问句的月份或年份对上记忆开头的日期就乘 1.25。")
    lines.append("")
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    del name


def main() -> None:
    which = sys.argv[1:] or ["LoCoMo", "LongMemEval-S"]
    OUT.mkdir(parents=True, exist_ok=True)
    loaders = {"LoCoMo": _locomo_samples, "LongMemEval-S": _longmemeval_samples}
    payload = {
        "scorer": "search() only; plain / stem / dated",
        "note": "not the product recall board",
        "datasets": {},
    }
    for name in which:
        started = time.monotonic()
        print(f"load {name}", flush=True)
        samples = loaders[name]()
        rows: list[dict] = []
        with (OUT / f"{name}.jsonl").open("w", encoding="utf-8") as sink:
            for index, sample in enumerate(samples, start=1):
                got = _score_sample(sample)
                for row in got:
                    sink.write(json.dumps(row, ensure_ascii=False) + "\n")
                sink.flush()
                rows.extend(got)
                if index == 1 or index == len(samples) or index % 50 == 0:
                    print(
                        f"{name} {index}/{len(samples)} elapsed={time.monotonic() - started:.0f}s",
                        flush=True,
                    )
        payload["datasets"][name] = {
            arm: _pack(_arm_rows(rows, arm)) for arm in ARMS
        }
        _write(name, rows, payload)
    _write("all", [], payload)


if __name__ == "__main__":
    main()
