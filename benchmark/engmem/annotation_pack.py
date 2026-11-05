"""EngMem-Bench 双人标注准备包：分层抽样、标注表导出、Cohen's κ 计算。

流程：
1. 导出标注表（默认 90 题，按 type x difficulty 分层、覆盖全部 21 仓）：

    python -X utf8 benchmark/engmem/annotation_pack.py --export=sheet.csv

   把 sheet.csv 交给第二标注者。标注者只对每题给结论：
   - verdict: correct / wrong-answer / wrong-anchor / unclear
   - annotator_answer: 认为参考答案是错的时候，给出正确答案（可选）

2. 回收后算一致性（第一标注者的 verdict 全为 correct 无意义，
   所以 κ 算的是「题目质量仲裁」场景：标注者 verdict vs 作者复核 verdict）：

    python -X utf8 benchmark/engmem/annotation_pack.py \
        --kappa=sheet_annotator.csv --against=sheet_author.csv

   两个 CSV 需同 qid 集合；κ 用 verdict 二列手算（不引第三方库）。

抽样种子固定（42），同一 bench.yaml 重跑导出结果一致。
"""

from __future__ import annotations

import csv
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BENCH = Path(__file__).resolve().parent / "bench.yaml"

FIELDS = [
    "qid", "repo", "type", "difficulty", "question",
    "reference_answer", "gold_anchor", "verdict", "annotator_answer",
]

INSTRUCTIONS = """# EngMem-Bench 第二标注者说明

你拿到的是 sheet.csv，每行一道题。请对每一行：

1. 读 question 和 reference_answer。
2. 打开该仓库（克隆地址见 repos.yaml，commit 已钉版）里 gold_anchor
   指向的文件节（格式 `文件::节标题`，同名节看 `@N`），核对答案。
3. verdict 填：
   - correct        参考答案与该节内容一致
   - wrong-answer   该节内容不支持参考答案
   - wrong-anchor   锚点指错了节（答案本身可能在别处是对的）
   - unclear        题目本身有歧义
4. 如果你填了 wrong-* ，请在 annotator_answer 里写你认为的正确答案。
5. 不要和第一标注者讨论，独立判断。

大约 90 题，预计 2-3 小时。
"""


def _load():
    return yaml.safe_load(BENCH.read_text(encoding="utf-8"))


def _export(out: str, n: int) -> None:
    data = _load()
    pool = []
    for repo in data["repos"]:
        for q in repo["questions"]:
            pool.append({
                "qid": q["id"], "repo": repo["name"], "type": q["type"],
                "difficulty": q.get("difficulty") or "medium",
                "question": q["question"],
                "reference_answer": q.get("answer") or "",
                "gold_anchor": "; ".join(q.get("gold") or []),
            })
    rng = random.Random(42)
    # 分层：type x difficulty 桶内随机，按桶比例分配，保底每桶 1 题
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in pool:
        buckets[(row["type"], row["difficulty"])].append(row)
    picked: list[dict] = []
    per_bucket = max(1, n // len(buckets))
    for key in sorted(buckets):
        rows = buckets[key]
        rng.shuffle(rows)
        picked.extend(rows[:per_bucket])
    # 覆盖全部仓库：缺的仓各补 1 题
    have = {r["repo"] for r in picked}
    for repo in data["repos"]:
        if repo["name"] in have:
            continue
        cands = [r for r in pool if r["repo"] == repo["name"]]
        if cands:
            picked.append(rng.choice(cands))
    picked = picked[:n]
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in picked:
            writer.writerow({**row, "verdict": "", "annotator_answer": ""})
    counts = Counter(r["type"] for r in picked)
    repos = len({r["repo"] for r in picked})
    print(f"exported {len(picked)} questions -> {out} "
          f"(types={dict(counts)}, repos={repos}/21)", flush=True)
    instr = Path(out).with_suffix(".instructions.md")
    instr.write_text(INSTRUCTIONS, encoding="utf-8")
    print(f"instructions -> {instr}", flush=True)


def _kappa(a_path: str, b_path: str) -> None:
    def load(p: str) -> dict[str, str]:
        out = {}
        for row in csv.DictReader(open(p, encoding="utf-8-sig")):
            out[row["qid"]] = (row.get("verdict") or "").strip()
        return out

    a, b = load(a_path), load(b_path)
    common = sorted(set(a) & set(b))
    missing_a = set(b) - set(a)
    missing_b = set(a) - set(b)
    if missing_a or missing_b:
        print(f"warning: qid 不对齐 a-only={len(missing_b)} b-only={len(missing_a)}",
              flush=True)
    pairs = [(a[q], b[q]) for q in common if a[q] and b[q]]
    n = len(pairs)
    if n == 0:
        print("no overlapping verdicts", flush=True)
        sys.exit(2)
    agree = sum(1 for x, y in pairs if x == y)
    po = agree / n
    ca, cb = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
    pe = sum(ca[k] / n * cb[k] / n for k in set(ca) | set(cb))
    kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    print(f"n={n} agreement={agree} po={po:.3f} pe={pe:.3f} "
          f"Cohen's kappa={kappa:.3f}", flush=True)
    print("landis-koch: <0 poor, 0-0.2 slight, 0.21-0.4 fair, "
          "0.41-0.6 moderate, 0.61-0.8 substantial, 0.81-1 almost perfect",
          flush=True)


def _fleiss(paths: list[str]) -> None:
    """多标注者 Fleiss' κ + 逐对 Cohen's κ + 多数投票结果。"""
    sheets: list[dict[str, str]] = []
    for p in paths:
        m = {}
        for row in csv.DictReader(open(p, encoding="utf-8-sig")):
            m[row["qid"]] = (row.get("verdict") or "").strip()
        sheets.append(m)
    common = sorted(set.intersection(*[set(s) for s in sheets]))
    common = [q for q in common if all(s[q] for s in sheets)]
    n = len(common)
    k = len(sheets)
    if n == 0 or k < 2:
        print("标注者或共同 qid 不足", flush=True)
        sys.exit(2)
    cats = sorted({s[q] for s in sheets for q in common})
    # Fleiss: 每题各类别票数
    p_j = {c: sum(1 for q in common for s in sheets if s[q] == c) / (n * k)
           for c in cats}
    pe = sum(v * v for v in p_j.values())
    p_i = []
    for q in common:
        votes = Counter(s[q] for s in sheets)
        p_i.append(sum(c * (c - 1) for c in votes.values()) / (k * (k - 1)))
    po = sum(p_i) / n
    kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    print(f"annotators={k} n={n} categories={cats}", flush=True)
    print(f"Fleiss' kappa={kappa:.3f} (po={po:.3f} pe={pe:.3f})", flush=True)
    # 逐对 Cohen
    for i in range(k):
        for j in range(i + 1, k):
            pairs = [(sheets[i][q], sheets[j][q]) for q in common]
            agree = sum(1 for x, y in pairs if x == y)
            ca, cb = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
            pe2 = sum(ca[c] / n * cb[c] / n for c in set(ca) | set(cb))
            k2 = (agree / n - pe2) / (1 - pe2) if pe2 < 1 else float("nan")
            print(f"{Path(paths[i]).stem} vs {Path(paths[j]).stem} "
                  f"agree={agree}/{n} Cohen={k2:.3f}", flush=True)
    # 多数投票与分歧题
    disagreements = []
    for q in common:
        votes = Counter(s[q] for s in sheets)
        top, cnt = votes.most_common(1)[0]
        if len(votes) > 1:
            disagreements.append((q, dict(votes), top))
    print(f"完全一致: {n - len(disagreements)}/{n}；有分歧: {len(disagreements)}",
          flush=True)
    for q, votes, top in disagreements:
        print(f"  {q}: {votes} -> 多数={top}", flush=True)



def main() -> None:
    export_to = None
    kappa = None
    against = None
    fleiss = None
    n = 90
    for arg in sys.argv[1:]:
        if arg.startswith("--export="):
            export_to = arg.split("=", 1)[1]
        elif arg.startswith("--kappa="):
            kappa = arg.split("=", 1)[1]
        elif arg.startswith("--against="):
            against = arg.split("=", 1)[1]
        elif arg.startswith("--fleiss="):
            fleiss = arg.split("=", 1)[1]
        elif arg.startswith("--n="):
            n = int(arg.split("=", 1)[1])
        else:
            print(f"unknown arg: {arg}", flush=True)
            sys.exit(2)
    if export_to:
        _export(export_to, n)
    elif fleiss:
        _fleiss([p for p in fleiss.split(",") if p])
    elif kappa and against:
        _kappa(kappa, against)
    else:
        print(__doc__, flush=True)


if __name__ == "__main__":
    main()
