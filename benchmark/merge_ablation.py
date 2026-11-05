"""消融榜合并：读取 results/ablate-<cfg>/summary.json，合成一张对比表。

    python -X utf8 benchmark/merge_ablation.py --out=ablation-20261104 \
        --configs=full,expansion,title,weakfilter,temporal,context

每个 cfg 对应 benchmark/results/ablate-<cfg>/ 下由 recall_board 跑出的榜
（同一 limit 口径）。输出 out/SUMMARY.md + summary.json。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmark" / "results"

DATASETS = ("LoCoMo", "LongMemEval-S")
METRICS = ("hit@5", "recall@5", "mrr", "hit@10", "recall@10")


def _fmt(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def main() -> None:
    out = RESULTS / "ablation-dev"
    configs = ["full"]
    for arg in sys.argv[1:]:
        if arg.startswith("--out="):
            out = RESULTS / arg.split("=", 1)[1]
        elif arg.startswith("--configs="):
            configs = [c.strip() for c in arg.split("=", 1)[1].split(",") if c.strip()]
        else:
            print(f"unknown arg: {arg}", flush=True)
            sys.exit(2)

    boards: dict[str, dict] = {}
    missing = []
    for cfg in configs:
        path = RESULTS / f"ablate-{cfg}" / "summary.json"
        if not path.is_file():
            missing.append(cfg)
            continue
        boards[cfg] = json.loads(path.read_text(encoding="utf-8"))
    if missing:
        print(f"missing configs: {missing}", flush=True)
    if "full" not in boards:
        print("full 配置缺失，无法出对比表", flush=True)
        sys.exit(2)

    out.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 消融榜 · {out.name.replace('ablation-', '')}",
        "",
        "同一口径（LoCoMo 全量 10 样本 + LongMemEval-S 前 50 样本），每行关掉",
        "一个组件（RSI_ABLATE）。ΔHit@5 为该行相对 full 的百分点差。",
        "",
    ]
    summary: dict = {"configs": {}}
    for dataset in DATASETS:
        lines.append(f"## {dataset}")
        lines.append("")
        lines.append("| 配置 | 题数 | Hit@5 | Recall@5 | MRR | Hit@10 | Recall@10 | ΔHit@5 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        base = boards["full"]["datasets"][dataset]["hit@5"]
        for cfg in configs:
            if cfg not in boards:
                continue
            packed = boards[cfg]["datasets"].get(dataset)
            if not packed:
                continue
            delta = packed["hit@5"] - base
            label = {"full": "full 最终（v2+引用降权）", "fullv1": "full v1（修复前）", "noqdisc": "仅 v2（无引用降权）"}.get(cfg, f"−{cfg}")
            lines.append(
                f"| {label} | {packed['scored']} | {_fmt(packed['hit@5'])} | "
                f"{_fmt(packed['recall@5'])} | {packed['mrr']:.3f} | "
                f"{_fmt(packed['hit@10'])} | {_fmt(packed['recall@10'])} | "
                f"{delta * 100:+.1f} |"
            )
            summary["configs"].setdefault(cfg, {})[dataset] = packed
        lines.append("")
        per_type = boards["full"]["datasets"][dataset].get("per_type") or {}
        if per_type:
            types = sorted(per_type)
            lines.append(f"### {dataset} 分题型 Hit@5")
            lines.append("")
            lines.append("| 配置 | " + " | ".join(types) + " |")
            lines.append("|---|" + "---:|" * len(types))
            for cfg in configs:
                if cfg not in boards:
                    continue
                pt = boards[cfg]["datasets"][dataset].get("per_type") or {}
                cells = []
                for t in types:
                    cell = pt.get(t) or {}
                    value = cell.get("hit@5")
                    cells.append("—" if value is None else f"{value * 100:.1f}%")
                label = {"full": "full 最终（v2+引用降权）", "fullv1": "full v1（修复前）", "noqdisc": "仅 v2（无引用降权）"}.get(cfg, f"−{cfg}")
                lines.append(f"| {label} | " + " | ".join(cells) + " |")
            lines.append("")
    lines += [
        "## 口径",
        "",
        "- full 行为内部一致性重跑（与官方主榜样本量不同：LME 取前 50），",
        "  与主榜的绝对值差异来自样本子集，不是管线变化。",
        "- context 开关只影响 LoCoMo 写入侧（turn 级上下文增强）。",
        "- fullv1 是上下文增强 v1（修复前）的历史榜；noqdisc 是仅 context-v2、",
        "  未开查询期引用降权的中间榜；full 是最终管线（v2 + 时间题引用降权）。",
    ]
    (out / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"done -> {out / 'SUMMARY.md'}", flush=True)


if __name__ == "__main__":
    main()
