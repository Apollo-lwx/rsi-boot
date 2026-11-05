"""受控 token 研究：RSI 记忆注入 vs 全量上下文的 token 消耗。

三种用法：

1. 零 Key 本地可跑（上下文规模测量，论文主表用它）：

    python -X utf8 benchmark/token_study.py --measure-context --out=token-20261103

   对 LoCoMo/LongMemEval 每题，比较两种臂的「送入模型的上下文 token 数」：
   - full-context 臂：该样本全部对话原文；
   - rsi 臂：RSI 冷启动召回 top-5 命中的原文。
   同一批题、同一 tokenizer、逐题配对，输出配对 Wilcoxon 符号秩检验
   （正态近似+连续性修正）、效应量 r 与中位数压缩比。>=60 题满足计划要求
   （LoCoMo 全量题目数远超 60）。

2. 真实 LLM-in-loop 研究（需要宿主与 API Key，脚本就绪）：

    python -X utf8 benchmark/token_study.py --prepare

   生成 prompts.jsonl（每题两臂、钉死模型与温度）与 RUNBOOK.md：干净临时
   HOME、固定模型、每题 3 轮、记录 usage.prompt_tokens 到 results.csv。

3. 分析真实跑数：

    python -X utf8 benchmark/token_study.py --analyze=results.csv

统计检验纯手算（正态近似 Wilcoxon），不引 scipy。
"""

from __future__ import annotations

import csv
import json
import math
import re
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.recall_board import (  # noqa: E402
    DATA,
    _locomo_samples,
    _longmemeval_samples,
    suppress_event_log,
)
from benchmark.run_baselines import SYSTEMS, _load  # noqa: E402

OUT = ROOT / "benchmark" / "results" / "token-dev"

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)

PINNED_MODEL = "gpt-4o-mini-2024-07-18"
PINNED_TEMPERATURE = 0.0
ROUNDS = 3


def _count_tokens(text: str) -> int:
    """token 计数：有 tiktoken 用 cl100k，否则 regex 近似并在注记里声明。"""
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # 近似：英文词/标点各计 1，CJK 字各计 1；系统性低估英文约 20%，
        # 但两臂同一把尺子，配对比较不受影响
        return len(_TOKEN_RE.findall(text))


def _tokenizer_note() -> str:
    try:
        import tiktoken  # noqa: F401

        return "tiktoken cl100k_base"
    except Exception:
        return "regex-approx (no tiktoken; same ruler for both arms)"


# ---------- 配对 Wilcoxon 符号秩（正态近似 + 连续性修正） ----------

def wilcoxon_signed_rank(diffs: list[float]) -> dict:
    pairs = [(abs(d), 1 if d > 0 else -1) for d in diffs if d != 0]
    n = len(pairs)
    if n == 0:
        return {"n": 0, "w_plus": 0.0, "z": None, "p_two_sided": None, "r": None}
    pairs.sort(key=lambda p: p[0])
    ranks: list[float] = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        mean_rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[k] = mean_rank
        i = j + 1
    w_plus = sum(r for r, (_, sign) in zip(ranks, pairs) if sign > 0)
    tie_counts: list[int] = []
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        tie_counts.append(j - i + 1)
        i = j + 1
    var = n * (n + 1) * (2 * n + 1) / 24.0
    var -= sum(t ** 3 - t for t in tie_counts) / 48.0
    if var <= 0:
        return {"n": n, "w_plus": w_plus, "z": None, "p_two_sided": None, "r": None}
    z = (w_plus - n * (n + 1) / 4.0 - 0.5) / math.sqrt(var)
    p = math.erfc(abs(z) / math.sqrt(2.0))  # 双侧
    return {"n": n, "w_plus": w_plus, "z": z, "p_two_sided": p,
            "r": abs(z) / math.sqrt(n)}


def _fmt_p(p: float | None) -> str:
    if p is None:
        return "—"
    return "<0.001" if p < 0.001 else f"{p:.4f}"


# ---------- 模式 1：零 Key 上下文规模测量 ----------

def _measure_context(datasets: list[str], limit: int | None,
                     shard: tuple[int, int] | None = None) -> None:
    loaders = {"LoCoMo": _locomo_samples, "LongMemEval-S": _longmemeval_samples}
    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "context_tokens.csv"
    # 增量合并：已有同 OUT 的结果读进来，本次跑的数据集整段替换，
    # 支持分多次跑不同数据集后汇总出一份统计。分片模式下各片独立成文件，
    # 由 --merge 汇总（单数据集全量超过前台超时上限时必须分片）。
    kept: list[dict] = []
    if shard is None and csv_path.exists():
        for row in csv.DictReader(csv_path.open(encoding="utf-8")):
            if row["dataset"] not in datasets:
                row["full_tokens"] = int(row["full_tokens"])
                row["rsi_tokens"] = int(row["rsi_tokens"])
                kept.append(row)
    adapter = _load(SYSTEMS["rsi"])
    all_rows: list[dict] = []
    started = time.monotonic()
    for dataset in datasets:
        samples = loaders[dataset]()
        if shard is not None:
            i, n = shard
            samples = [s for idx, s in enumerate(samples) if idx % n == i]
        if limit:
            samples = samples[:limit]
        work = DATA / "work" / f"token-{dataset}"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)
        adapter.open(work)
        try:
            with suppress_event_log():
                for index, sample in enumerate(samples, start=1):
                    adapter.reset()
                    rows = sample["rows"]
                    adapter.ingest(rows)
                    content_by_key = {key: content for key, _, content in rows}
                    full_tokens = sum(_count_tokens(c) for c in content_by_key.values())
                    for question in sample["questions"]:
                        top5 = adapter.retrieve(question["question"], 5)
                        rsi_tokens = sum(
                            _count_tokens(content_by_key[k]) for k in top5
                            if k in content_by_key
                        )
                        all_rows.append({
                            "dataset": dataset,
                            "qid": question["id"],
                            "full_tokens": full_tokens,
                            "rsi_tokens": rsi_tokens,
                        })
                    if index % 10 == 0:
                        print(
                            f"{dataset} {index}/{len(samples)} "
                            f"elapsed={time.monotonic() - started:.0f}s", flush=True,
                        )
        finally:
            adapter.close()

    if shard is not None:
        shard_path = OUT / f"context_tokens.shard{shard[0]}.csv"
        with shard_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["dataset", "qid", "full_tokens", "rsi_tokens"],
            )
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"shard -> {shard_path}", flush=True)
        return
    all_rows = kept + all_rows
    _finalize_context(all_rows)


def _merge_context_shards(datasets: list[str]) -> None:
    """把 --shard 产出的 context_tokens.shard<i>.csv 与未重跑数据集的存量行合并出总表。"""
    csv_path = OUT / "context_tokens.csv"
    kept: list[dict] = []
    if csv_path.exists():
        for row in csv.DictReader(csv_path.open(encoding="utf-8")):
            if row["dataset"] not in datasets:
                row["full_tokens"] = int(row["full_tokens"])
                row["rsi_tokens"] = int(row["rsi_tokens"])
                kept.append(row)
    rows = list(kept)
    for path in sorted(OUT.glob("context_tokens.shard*.csv")):
        for row in csv.DictReader(path.open(encoding="utf-8")):
            if row["dataset"] in datasets:
                row["full_tokens"] = int(row["full_tokens"])
                row["rsi_tokens"] = int(row["rsi_tokens"])
                rows.append(row)
    _finalize_context(rows)


def _finalize_context(all_rows: list[dict]) -> None:
    csv_path = OUT / "context_tokens.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["dataset", "qid", "full_tokens", "rsi_tokens"],
        )
        writer.writeheader()
        writer.writerows(all_rows)

    diffs = [r["full_tokens"] - r["rsi_tokens"] for r in all_rows]
    ratios = [
        r["rsi_tokens"] / r["full_tokens"] for r in all_rows if r["full_tokens"]
    ]
    stat = wilcoxon_signed_rank(diffs)
    summary = {
        "tokenizer": _tokenizer_note(),
        "questions": len(all_rows),
        "full_median": statistics.median(r["full_tokens"] for r in all_rows),
        "rsi_median": statistics.median(r["rsi_tokens"] for r in all_rows),
        "compression_median": statistics.median(ratios),
        "reduction_median_pct": (1 - statistics.median(ratios)) * 100,
        "wilcoxon": stat,
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    lines = [
        f"# 受控 token 研究 · {OUT.name.replace('token-', '')}",
        "",
        f"tokenizer: {summary['tokenizer']}（两臂同一把尺子，配对比较）",
        "",
        f"- 题数：{summary['questions']}（>=60 要求：{'满足' if summary['questions'] >= 60 else '不满足'}）",
        f"- full-context 臂上下文 token 中位数：{summary['full_median']:.0f}",
        f"- rsi 臂上下文 token 中位数：{summary['rsi_median']:.0f}",
        f"- 中位压缩比（rsi/full）：{summary['compression_median'] * 100:.1f}%"
        f"（即减少 {summary['reduction_median_pct']:.1f}%）",
        f"- 配对 Wilcoxon：n={stat['n']}，z={stat['z']:.2f}" if stat["z"] is not None
        else f"- 配对 Wilcoxon：n={stat['n']}",
        f"- p（双侧）：{_fmt_p(stat['p_two_sided'])}，效应量 r={stat['r']:.3f}"
        if stat["r"] is not None else "",
        "",
        "口径声明：这是「送入模型的上下文 token 数」测量，不含模型生成；"
        "真实 LLM-in-loop 的 usage token 研究用 --prepare 生成的协议执行。",
    ]
    (OUT / "SUMMARY.md").write_text(
        "\n".join(x for x in lines if x != "") + "\n", encoding="utf-8",
    )
    print(f"done -> {OUT / 'SUMMARY.md'}", flush=True)


# ---------- 模式 2：真实 LLM 研究准备 ----------

def _prepare() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="rsi-token-home-"))
    samples = _locomo_samples()
    prompts = []
    n = 0
    for sample in samples:
        for question in sample["questions"]:
            prompts.append({
                "qid": question["id"], "arm": "no-rsi",
                "question": question["question"],
            })
            prompts.append({
                "qid": question["id"], "arm": "with-rsi",
                "question": question["question"],
            })
            n += 1
    with (OUT / "prompts.jsonl").open("w", encoding="utf-8") as sink:
        for row in prompts:
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")
    (OUT / "RUNBOOK.md").write_text(f"""# 真实 LLM-in-loop token 研究协议

1. 干净环境：`HOME={home}`（已创建的空目录），不设任何用户级 MCP/配置；
   with-rsi 臂才在项目里挂 RSI。
2. 钉死模型 `{PINNED_MODEL}`，temperature={PINNED_TEMPERATURE}，
   seed 固定；全程不切换模型。
3. `prompts.jsonl` 每行一题一臂，每题每臂跑 {ROUNDS} 轮（round=1..3），
   逐次记录 `usage.prompt_tokens` / `usage.completion_tokens` /
   墙钟时间到 `results.csv`：
   `qid,arm,round,prompt_tokens,completion_tokens,wall_ms`
4. 分析：`python -X utf8 benchmark/token_study.py --analyze=results.csv`
   （配对 Wilcoxon + 效应量；配对键=(qid,round)，两臂同窗比较）。
5. 注意：Cursor/Claude 宿主侧执行时，两臂必须同一宿主同一版本，
   避免宿主差异污染。
""", encoding="utf-8")
    print(f"prepared {n} questions x 2 arms -> {OUT / 'prompts.jsonl'}", flush=True)
    print(f"clean HOME: {home}", flush=True)


# ---------- 模式 3：真实跑数分析 ----------

def _analyze(csv_name: str) -> None:
    path = Path(csv_name)
    if not path.is_absolute():
        path = OUT / csv_name
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    paired: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        key = (row["qid"], row["round"])
        paired.setdefault(key, {})[row["arm"]] = int(row["prompt_tokens"])
    diffs = [
        arms["no-rsi"] - arms["with-rsi"]
        for arms in paired.values()
        if "no-rsi" in arms and "with-rsi" in arms
    ]
    stat = wilcoxon_signed_rank(diffs)
    print(json.dumps({
        "pairs": len(diffs),
        "median_saving_tokens": statistics.median(diffs) if diffs else None,
        "wilcoxon": stat,
    }, ensure_ascii=False, indent=2), flush=True)


def main() -> None:
    global OUT
    args = sys.argv[1:]
    limit = None
    shard = None
    merge = False
    datasets = ["LoCoMo", "LongMemEval-S"]
    measure = False
    analyze = None
    prepare = False
    for arg in args:
        if arg == "--measure-context":
            measure = True
        elif arg == "--merge":
            merge = True
        elif arg == "--prepare":
            prepare = True
        elif arg.startswith("--analyze="):
            analyze = arg.split("=", 1)[1]
        elif arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])
        elif arg.startswith("--shard="):
            i, _, n = arg.split("=", 1)[1].partition("/")
            shard = (int(i), int(n))
        elif arg.startswith("--datasets="):
            datasets = [d.strip() for d in arg.split("=", 1)[1].split(",") if d.strip()]
        elif arg.startswith("--out="):
            OUT = ROOT / "benchmark" / "results" / arg.split("=", 1)[1]
        else:
            print(f"unknown arg: {arg}", flush=True)
            sys.exit(2)
    if measure and merge:
        _merge_context_shards(datasets)
    elif measure:
        _measure_context(datasets, limit, shard=shard)
    elif prepare:
        _prepare()
    elif analyze:
        _analyze(analyze)
    else:
        print(__doc__, flush=True)


if __name__ == "__main__":
    main()
