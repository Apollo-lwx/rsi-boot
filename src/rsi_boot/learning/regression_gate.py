"""回放集回归验证门禁（§3.9，提案生命周期 validating 阶段）。

回放集：近 30 天该 intent 抽样 50 条请求（含历史反馈标签）。
基线：优先复用日志中的历史响应摘录（response_excerpt，零模型成本）评估各维度；
候选：将提案 overlay 应用到对应槽位后重放（渲染 + 模型调用 + 质量评估）。

晋升条件：目标指标（质量分/意图匹配率）提升 **且无任何维度退化 > 5%**，否则 rejected。
accuracy 维度在回放中默认不评判（judge 成本翻倍），可配 gate.replay_accuracy: true 开启。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..data.sqlite import SQLiteClient
from ..knowledge.embedding import EmbeddingService
from ..model.adapter import ModelAdapter
from ..quality.assessor import QualityAssessor

logger = logging.getLogger(__name__)

REPLAY_DAYS = 30
REPLAY_SAMPLE_SIZE = 50
MAX_DEGRADATION = 0.05   # 单维度退化 > 5% 即否决
MIN_REPLAY_SAMPLES = 5   # 回放样本不足时门禁不通过（证据不足）


@dataclass
class RegressionReport:
    slot: str
    samples: int
    target_metric: str
    baseline_target: float
    candidate_target: float
    dimensions: Dict[str, Dict[str, float]] = field(default_factory=dict)  # dim → {baseline, candidate}
    passed: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot": self.slot, "samples": self.samples, "target_metric": self.target_metric,
            "baseline_target": round(self.baseline_target, 4),
            "candidate_target": round(self.candidate_target, 4),
            "dimensions": {k: {kk: round(vv, 4) for kk, vv in v.items()} for k, v in self.dimensions.items()},
            "passed": self.passed, "reason": self.reason,
        }


class RegressionGate:
    def __init__(
        self,
        db: SQLiteClient,
        adapter: ModelAdapter,
        embedding: EmbeddingService,
        config: Dict[str, Any],
    ):
        self._db = db
        self._adapter = adapter
        self._assessor = QualityAssessor(embedding, adapter, config)
        self._replay_accuracy = bool(config.get("gate", {}).get("replay_accuracy", False))
        self._default_model = str(config.get("model", {}).get("default", "gpt-4o-mini"))

    async def replay_sample(self, intent: str, days: int = REPLAY_DAYS, n: int = REPLAY_SAMPLE_SIZE) -> List[Dict[str, Any]]:
        """近 N 天该 intent 随机抽样（含历史反馈标签与响应摘录）"""
        conn = await self._db.connect()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with conn.execute(
            "SELECT raw_input, intent, response_excerpt, feedback_action, feedback_rating, quality_score "
            "FROM interaction_logs WHERE intent = ? AND status = 'success' AND created_at >= ? "
            "ORDER BY RANDOM() LIMIT ?",
            (intent, since, n),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def evaluate(self, proposal: Dict[str, Any]) -> RegressionReport:
        """对提案执行回放验证。proposal: {slot, target_ref, action, payload(after), intent}"""
        slot = proposal["slot"]
        samples = await self.replay_sample(proposal.get("intent") or "")
        report = RegressionReport(
            slot=slot, samples=len(samples),
            target_metric="match_rate" if slot == "intent_rule" else "quality_score",
            baseline_target=0.0, candidate_target=0.0,
        )
        if len(samples) < MIN_REPLAY_SAMPLES:
            report.reason = f"回放样本不足（{len(samples)} < {MIN_REPLAY_SAMPLES}），证据不足不通过"
            return report

        if slot == "intent_rule":
            return await self._eval_intent_rule(proposal, samples, report)
        if slot in ("knowledge", "skill"):
            return await self._eval_injection(proposal, samples, report)
        return await self._eval_response_quality(proposal, samples, report)

    # ---------- 各槽位回放评估 ----------

    async def _eval_response_quality(
        self, proposal: Dict[str, Any], samples: List[Dict[str, Any]], report: RegressionReport
    ) -> RegressionReport:
        """prompt_template / strategy：候选 overlay 重新生成响应，与历史响应比质量维度"""
        dims: Dict[str, List[float]] = {"relevance": [], "actionability": [], "quality": []}
        cand_dims: Dict[str, List[float]] = {"relevance": [], "actionability": [], "quality": []}
        for s in samples:
            base_score = await self._assess_historical(s)
            cand_score = await self._run_candidate(proposal, s["raw_input"])
            if base_score is None or cand_score is None:
                continue
            for attr, dim in (("relevance", "relevance"), ("actionability", "actionability"),
                              ("quality_score", "quality")):
                b, c = getattr(base_score, attr), getattr(cand_score, attr)
                if b is not None and c is not None:
                    dims[dim].append(b)
                    cand_dims[dim].append(c)
        return self._verdict(report, dims, cand_dims)

    async def _eval_intent_rule(
        self, proposal: Dict[str, Any], samples: List[Dict[str, Any]], report: RegressionReport
    ) -> RegressionReport:
        """intent_rule：候选规则 overlay 重分类，与历史意图标签比对匹配率。
        基线 = 现行规则对历史标签的匹配率；候选 = overlay 规则优先命中（→ 提案 intent），
        未命中回落现行规则"""
        from ..preprocessor.intent_classifier import detect_intent

        after_rules = (proposal.get("payload") or {}).get("after") or []
        compiled = self._compile_overlay_rules(after_rules)
        target_intent = proposal.get("intent") or ""
        baseline_hits = candidate_hits = total = 0
        for s in samples:
            if not s["intent"]:
                continue
            total += 1
            base_intent, _ = detect_intent(s["raw_input"])
            baseline_hits += int(base_intent == s["intent"])
            if compiled and any(p.search(s["raw_input"]) for p in compiled):
                cand_intent = target_intent
            else:
                cand_intent = base_intent
            candidate_hits += int(cand_intent == s["intent"])
        if total < MIN_REPLAY_SAMPLES:
            report.reason = "有效样本不足"
            return report
        report.baseline_target = baseline_hits / total
        report.candidate_target = candidate_hits / total
        report.dimensions = {"match_rate": {"baseline": report.baseline_target, "candidate": report.candidate_target}}
        report.passed = report.candidate_target > report.baseline_target
        report.reason = "匹配率提升" if report.passed else "匹配率无提升，拒绝"
        return report

    async def _eval_injection(
        self, proposal: Dict[str, Any], samples: List[Dict[str, Any]], report: RegressionReport
    ) -> RegressionReport:
        """knowledge / skill：候选注入内容与历史查询的 relevance 均值 vs 历史响应 relevance 基线"""
        content = str((proposal.get("payload") or {}).get("after", {}).get("content", ""))
        if not content:
            report.reason = "提案缺少注入内容"
            return report
        baseline_rel: List[float] = []
        candidate_rel: List[float] = []
        for s in samples:
            vectors = await self._assessor._embedding.embed([s["raw_input"][:4000], content[:4000]])
            if vectors[0] is None or vectors[1] is None:
                continue
            from ..quality.assessor import _rescale_cosine
            import numpy as np
            denom = float(np.linalg.norm(vectors[0]) * np.linalg.norm(vectors[1]))
            if denom < 1e-12:
                continue
            candidate_rel.append(_rescale_cosine(float(np.dot(vectors[0], vectors[1]) / denom)))
            base = await self._assess_historical(s)
            if base is not None and base.relevance is not None:
                baseline_rel.append(base.relevance)
        if not candidate_rel or not baseline_rel:
            report.reason = "embedding 不可用，无法评估"
            return report
        report.baseline_target = sum(baseline_rel) / len(baseline_rel)
        report.candidate_target = sum(candidate_rel) / len(candidate_rel)
        report.dimensions = {"relevance": {"baseline": report.baseline_target, "candidate": report.candidate_target}}
        improved = report.candidate_target > report.baseline_target
        degraded = report.baseline_target - report.candidate_target > MAX_DEGRADATION
        report.passed = improved and not degraded
        report.reason = "注入相关性提升" if report.passed else "注入相关性无提升或退化超阈"
        return report

    # ---------- 样本级评估 ----------

    async def _assess_historical(self, sample: Dict[str, Any]):
        """基线：历史响应摘录的质量维度（无摘录则无法评估，返回 None）"""
        excerpt = sample.get("response_excerpt")
        if not excerpt:
            return None
        return await self._assessor.assess(
            sample["raw_input"], excerpt, sample.get("intent"), role=None, feedback_token=None,
        )

    async def _run_candidate(self, proposal: Dict[str, Any], raw_input: str):
        """候选：overlay 应用到模型/模板后重新生成并评估（模板渲染由调用方注入内容差异）"""
        after = (proposal.get("payload") or {}).get("after") or {}
        model = after.get("model_name") or self._default_model
        # 模板/策略差异体现在 system 提示词；候选模板内容直接作为 system（最小可回放形态）
        system = after.get("template_content") or after.get("system_prompt") or ""
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": raw_input}
        ]
        try:
            result = await self._adapter.complete(model, messages)
        except Exception as exc:
            logger.warning("候选回放模型调用失败: %s", exc)
            return None
        return await self._assessor.assess(raw_input, result.text, proposal.get("intent"), role=None)

    @staticmethod
    def _compile_overlay_rules(after_rules: List[Dict[str, Any]]) -> List[re.Pattern]:
        patterns: List[re.Pattern] = []
        for rule in after_rules:
            for p in rule.get("patterns", []):
                try:
                    patterns.append(re.compile(p, re.IGNORECASE))
                except re.error:
                    continue
        return patterns

    def _verdict(
        self, report: RegressionReport,
        baseline: Dict[str, List[float]], candidate: Dict[str, List[float]],
    ) -> RegressionReport:
        """晋升判定：目标指标提升且无任何维度退化 > 5%"""
        if not baseline.get("quality") or not candidate.get("quality"):
            report.reason = "无可评估样本（历史响应摘录缺失或候选生成失败）"
            return report
        for dim in baseline:
            if not baseline[dim] or not candidate[dim]:
                continue
            b_mean = sum(baseline[dim]) / len(baseline[dim])
            c_mean = sum(candidate[dim]) / len(candidate[dim])
            report.dimensions[dim] = {"baseline": b_mean, "candidate": c_mean}
        report.baseline_target = report.dimensions["quality"]["baseline"]
        report.candidate_target = report.dimensions["quality"]["candidate"]
        improved = report.candidate_target > report.baseline_target
        degraded_dims = [
            d for d, v in report.dimensions.items()
            if v["baseline"] - v["candidate"] > MAX_DEGRADATION
        ]
        report.passed = improved and not degraded_dims
        if not improved:
            report.reason = "目标指标未提升"
        elif degraded_dims:
            report.reason = f"维度退化超 5%：{', '.join(degraded_dims)}"
        else:
            report.reason = "目标指标提升且无维度退化"
        return report
