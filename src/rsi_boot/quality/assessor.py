"""三维度质量评估（§3.5，P3.5）。

- relevance：query/response 各截断 4000 字符嵌入，cosine 经 max(0, (s-0.2)/0.6)
  重标定到 [0,1]；每次响应都计算（embedding 不可用则该维度缺席，权重归一化）
- accuracy：LLM-as-judge（默认 gpt-4o-mini），抽样 10% 执行；用户差评（rating ≤ 2）
  必查——响应时把评判上下文存入 TTL 缓存，反馈到达时取出补查并回写 quality_score
- actionability：规则加权——带语言标记代码块 +0.5；分步骤列表（≥3 项）+0.3；
  正文长度 ≥ 意图下限（code_gen 100 / explain 200 / 默认 100 字符）+0.2

综合分 = Σ(weight_i × score_i)，权重按角色取自 config/quality_rubric.yaml；
缺席维度不参与，按其余维度权重归一化折算。
"""

from __future__ import annotations

import json
import logging
import random
import re
from importlib import resources
from typing import Any, Dict, List, Optional

import numpy as np
import yaml
from cachetools import TTLCache
from pydantic import BaseModel

from ..knowledge.embedding import EmbeddingService
from ..model.adapter import ModelAdapter

logger = logging.getLogger(__name__)

_JUDGE_TRUNCATE = 4000          # relevance 嵌入与 judge 提示词的文本截断
_JUDGE_CACHE_SIZE = 1000        # 差评必查上下文缓存（feedback_token → 上下文）
_JUDGE_CACHE_TTL_S = 3600

_CODE_BLOCK_RE = re.compile(r"```[a-zA-Z][a-zA-Z0-9+#-]*\n")
_STEP_LINE_RE = re.compile(r"^\s*(?:\d+[.、)]|[-*])\s+\S", re.MULTILINE)

#: 正文长度下限（字符），按 intent 启用；未列出的 intent 用 default
_LENGTH_MIN_BY_INTENT: Dict[str, int] = {"code_gen": 100, "explain": 200, "default": 100}

_JUDGE_PROMPT = """你是事实一致性评审。给定用户问题、AI 回答与检索到的参考知识，判断回答的事实准确性。

【用户问题】
{query}

【AI 回答】
{response}

【参考知识】
{knowledge}

仅输出 JSON：{{"score": 0.0~1.0, "reason": "一句话依据"}}。score 1.0 表示事实完全准确，0.0 表示严重错误。"""


class QualityResult(BaseModel):
    relevance: Optional[float] = None
    accuracy: Optional[float] = None
    actionability: float
    quality_score: float
    accuracy_sampled: bool = False
    judge_reason: Optional[str] = None


def _rescale_cosine(s: float) -> float:
    """cosine 0.2 以下视为不相关，0.8 以上视为满分"""
    return min(1.0, max(0.0, (s - 0.2) / 0.6))


def actionability_score(text: str, intent: Optional[str] = None) -> float:
    """规则检测加权（每次响应都计算，纯规则无成本）"""
    score = 0.0
    if _CODE_BLOCK_RE.search(text):
        score += 0.5
    if len(_STEP_LINE_RE.findall(text)) >= 3:
        score += 0.3
    threshold = _LENGTH_MIN_BY_INTENT.get(intent or "", _LENGTH_MIN_BY_INTENT["default"])
    if len(text) >= threshold:
        score += 0.2
    return min(1.0, score)


def _load_rubric() -> Dict[str, Dict[str, float]]:
    text = (resources.files("rsi_boot") / "config" / "quality_rubric.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    return {str(k): {d: float(w) for d, w in v.items()} for k, v in data.items() if isinstance(v, dict)}


class QualityAssessor:
    def __init__(self, embedding: EmbeddingService, adapter: ModelAdapter, config: dict[str, Any]):
        self._embedding = embedding
        self._adapter = adapter
        q_cfg = config.get("quality", {})
        self._sample_rate = float(q_cfg.get("accuracy_sample_rate", 0.1))
        self._judge_model = str(q_cfg.get("judge_model", "gpt-4o-mini"))
        self._rubric = _load_rubric()
        # 差评必查：accuracy 未抽中时的评判上下文（feedback_token → 上下文）
        self._pending_judge: TTLCache[str, Dict[str, Any]] = TTLCache(_JUDGE_CACHE_SIZE, _JUDGE_CACHE_TTL_S)

    async def assess(
        self,
        query: str,
        response_text: str,
        intent: Optional[str],
        role: Optional[str],
        retrieved_docs: Optional[List[Any]] = None,
        feedback_token: Optional[str] = None,
        force_accuracy: bool = False,
    ) -> QualityResult:
        relevance = await self._relevance(query, response_text)
        actionability = actionability_score(response_text, intent)

        accuracy: Optional[float] = None
        judge_reason: Optional[str] = None
        sampled = force_accuracy or random.random() < self._sample_rate
        if sampled:
            accuracy, judge_reason = await self.judge_accuracy(query, response_text, retrieved_docs)
        elif feedback_token:
            # 未抽中：暂存上下文，差评（rating ≤ 2）到达时必查（§3.5）
            self._pending_judge[feedback_token] = {
                "query": query,
                "response_text": response_text,
                "intent": intent,
                "role": role,
                "retrieved_docs": retrieved_docs,
                "relevance": relevance,
                "actionability": actionability,
            }

        score = self._combine(role, relevance, accuracy, actionability)
        return QualityResult(
            relevance=relevance, accuracy=accuracy, actionability=actionability,
            quality_score=score, accuracy_sampled=accuracy is not None, judge_reason=judge_reason,
        )

    async def judge_negative_feedback(self, feedback_token: str) -> Optional[QualityResult]:
        """差评必查：从暂存上下文补做 accuracy 评判，返回含 accuracy 的完整结果"""
        ctx = self._pending_judge.pop(feedback_token, None)
        if ctx is None:
            return None  # 上下文已过期或当时已抽样，无法补查
        accuracy, reason = await self.judge_accuracy(
            ctx["query"], ctx["response_text"], ctx["retrieved_docs"]
        )
        if accuracy is None:
            return None
        score = self._combine(ctx["role"], ctx["relevance"], accuracy, ctx["actionability"])
        return QualityResult(
            relevance=ctx["relevance"], accuracy=accuracy, actionability=ctx["actionability"],
            quality_score=score, accuracy_sampled=True, judge_reason=reason,
        )

    async def judge_accuracy(
        self, query: str, response_text: str, retrieved_docs: Optional[List[Any]] = None
    ) -> tuple[Optional[float], Optional[str]]:
        """LLM-as-judge 事实一致性检查；解析失败返回 (None, None) 按未采样处理"""
        knowledge = "\n".join(
            f"- {getattr(d, 'title', '')}: {getattr(d, 'body', '')[:200]}" for d in (retrieved_docs or [])[:5]
        ) or "（无）"
        messages = [{"role": "user", "content": _JUDGE_PROMPT.format(
            query=query[:_JUDGE_TRUNCATE], response=response_text[:_JUDGE_TRUNCATE], knowledge=knowledge,
        )}]
        try:
            result = await self._adapter.complete(self._judge_model, messages)
            data = json.loads(result.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
            score = float(data["score"])
            return min(1.0, max(0.0, score)), str(data.get("reason", ""))[:200]
        except Exception as exc:
            logger.warning("accuracy 评判失败（按未采样处理）: %s", exc)
            return None, None

    async def _relevance(self, query: str, response_text: str) -> Optional[float]:
        vectors = await self._embedding.embed([query[:_JUDGE_TRUNCATE], response_text[:_JUDGE_TRUNCATE]])
        if vectors[0] is None or vectors[1] is None:
            return None
        q, r = vectors[0], vectors[1]
        denom = float(np.linalg.norm(q) * np.linalg.norm(r))
        if denom < 1e-12:
            return None
        return _rescale_cosine(float(np.dot(q, r) / denom))

    def _combine(
        self, role: Optional[str], relevance: Optional[float], accuracy: Optional[float], actionability: float
    ) -> float:
        weights = self._rubric.get(role or "", self._rubric["default"])
        parts: List[tuple[float, float]] = [(weights.get("actionability", 0.25), actionability)]
        if relevance is not None:
            parts.append((weights.get("relevance", 0.4), relevance))
        if accuracy is not None:
            parts.append((weights.get("accuracy", 0.35), accuracy))
        total_w = sum(w for w, _ in parts)
        if total_w <= 0:
            return 0.0
        return round(sum(w * s for w, s in parts) / total_w, 4)
