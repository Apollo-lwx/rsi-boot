"""评测 harness（§12）：指标计算 + 基线对比 + 报告归档。

指标：意图 Top-1 准确率 / 宏 F1 / 混淆矩阵；检索 Recall@K / MRR；
Thompson 统计仿真（§12.3：1000 轮 × 100 次独立重复，非确定性断言）。
基线管理：baselines.json 版本化；回归门槛 = 不低于上一基线 2pp（§12.1/§12.2）。
报告归档 tests/eval/reports/（JSON，带时间戳）。
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

EVAL_DIR = Path(__file__).parent
DATASETS = EVAL_DIR / "datasets"
REPORTS = EVAL_DIR / "reports"
BASELINES_PATH = EVAL_DIR / "baselines.json"

REGRESSION_TOLERANCE = 0.02  # 回归门槛 2pp（§12.1/§12.2）


def load_jsonl(path: Path) -> List[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_baselines() -> Dict[str, Any]:
    return json.loads(BASELINES_PATH.read_text(encoding="utf-8"))


# ---------- 意图分类指标（§12.1） ----------


def intent_metrics(pairs: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    """pairs = [(预测, 真实)] → top1 准确率 + 宏 F1 + 混淆矩阵"""
    labels = sorted({t for _, t in pairs} | {p for p, _ in pairs})
    correct = sum(1 for p, t in pairs if p == t)
    f1_per_label: Dict[str, float] = {}
    for label in labels:
        tp = sum(1 for p, t in pairs if p == label and t == label)
        fp = sum(1 for p, t in pairs if p == label and t != label)
        fn = sum(1 for p, t in pairs if p != label and t == label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1_per_label[label] = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    confusion: Dict[str, Dict[str, int]] = {t: Counter() for t in labels}
    for p, t in pairs:
        confusion[t][p] = confusion[t].get(p, 0) + 1
    return {
        "top1": correct / len(pairs) if pairs else 0.0,
        "macro_f1": sum(f1_per_label.values()) / len(f1_per_label) if f1_per_label else 0.0,
        "per_intent_f1": f1_per_label,
        "confusion": {t: dict(c) for t, c in confusion.items()},
        "samples": len(pairs),
    }


# ---------- 检索指标（§12.2） ----------


def recall_at_k(ranked_ids: Sequence[str], relevant: Sequence[str], k: int) -> float:
    if not relevant:
        return 0.0
    hit = set(ranked_ids[:k]) & set(relevant)
    return len(hit) / len(relevant)


def reciprocal_rank(ranked_ids: Sequence[str], relevant: Sequence[str]) -> float:
    rel = set(relevant)
    for i, rid in enumerate(ranked_ids):
        if rid in rel:
            return 1.0 / (i + 1)
    return 0.0


def retrieval_metrics(
    results: Sequence[Tuple[List[str], List[str], str]], k: int = 5
) -> Dict[str, Any]:
    """results = [(命中排序 id 列表, 相关 id 列表, 项目类型)] → Recall@K / MRR（按项目类型分层）"""
    overall_recall, overall_rr = [], []
    per_type: Dict[str, Dict[str, List[float]]] = {}
    for ranked, relevant, ptype in results:
        r = recall_at_k(ranked, relevant, k)
        rr = reciprocal_rank(ranked, relevant)
        overall_recall.append(r)
        overall_rr.append(rr)
        bucket = per_type.setdefault(ptype, {"recall": [], "rr": []})
        bucket["recall"].append(r)
        bucket["rr"].append(rr)
    return {
        f"recall@{k}": sum(overall_recall) / len(overall_recall) if overall_recall else 0.0,
        "mrr": sum(overall_rr) / len(overall_rr) if overall_rr else 0.0,
        "per_project_type": {
            t: {f"recall@{k}": sum(v["recall"]) / len(v["recall"]), "mrr": sum(v["rr"]) / len(v["rr"]), "n": len(v["recall"])}
            for t, v in per_type.items()
        },
        "queries": len(results),
    }


# ---------- Thompson Sampling 仿真（§12.3） ----------


def simulate_thompson(
    arm_probs: Sequence[float],
    rounds: int = 1000,
    repeats: int = 100,
    seed: int = 42,
) -> Dict[str, float]:
    """合成 Bernoulli 臂环境：最优臂选择概率 + 累积 regret 对比均匀随机基线。

    更新规则与 §3.3 一致：reward>0 alpha+=reward；reward<0 beta+=|reward|（此处
    Bernoulli reward ∈ {0,1}，0 不更新）。
    """
    rng = random.Random(seed)
    best_arm = max(range(len(arm_probs)), key=lambda i: arm_probs[i])
    best_prob = arm_probs[best_arm]
    optimal_regret_per_round = 0.0  # 每轮期望 regret 相对最优臂

    best_pick_at_end = 0
    total_regret = 0.0
    random_regret = 0.0

    for _ in range(repeats):
        alphas = [1.0] * len(arm_probs)
        betas = [1.0] * len(arm_probs)
        picks = [0] * len(arm_probs)
        regret = 0.0
        regret_rand = 0.0
        for _ in range(rounds):
            samples = [rng.betavariate(a, b) for a, b in zip(alphas, betas)]
            arm = max(range(len(arm_probs)), key=lambda i: samples[i])
            picks[arm] += 1
            reward = 1.0 if rng.random() < arm_probs[arm] else 0.0
            if reward > 0:
                alphas[arm] += reward
            regret += best_prob - arm_probs[arm]
            # 均匀随机基线
            rand_arm = rng.randrange(len(arm_probs))
            regret_rand += best_prob - arm_probs[rand_arm]
        if picks.index(max(picks)) == best_arm:
            best_pick_at_end += 1
        total_regret += regret
        random_regret += regret_rand

    return {
        "best_arm_selection": best_pick_at_end / repeats,
        "avg_regret": total_regret / repeats,
        "avg_regret_random": random_regret / repeats,
        "regret_reduction": 1 - (total_regret / random_regret) if random_regret else 0.0,
        "rounds": rounds,
        "repeats": repeats,
    }


# ---------- 基线对比与报告 ----------


def check_against_baseline(metrics: Dict[str, float], baseline: Dict[str, float]) -> List[str]:
    """返回违规列表：低于基线 2pp 即回归（§12.1/§12.2 回归门槛）"""
    violations = []
    for key, base_value in baseline.items():
        current = metrics.get(key)
        if current is None:
            continue
        if current < base_value - REGRESSION_TOLERANCE:
            violations.append(f"{key}: {current:.4f} < 基线 {base_value:.4f} - 2pp")
    return violations


def write_report(name: str, payload: Dict[str, Any]) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORTS / f"{name}-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
