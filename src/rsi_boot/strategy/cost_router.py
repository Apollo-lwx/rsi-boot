"""成本路由（§6.1/§6.2，P4.2）：意图级成本倾向的自动模型选择。

§6.2：code_gen 可用高价模型，general_assist 仅允许低价模型——策略路由规则体现，
非硬限制。实现为「价格天花板 +  cheapest-in-tier」：意图映射到价格层级，
层级定义 completion 单价上限，路由在层级内选最便宜的已定价模型。

默认关闭（cost_routing.enabled: false）；开启后仅作用于策略引擎的兜底路由
（已有策略行命中的模型选择不受影响——策略显式意图优先于成本倾向）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..config.loader import load_model_prices

logger = logging.getLogger(__name__)

#: 意图 → 价格层级默认映射（可被 cost_routing.intent_tier 覆盖）
_DEFAULT_INTENT_TIER: Dict[str, str] = {
    "general_assist": "low",
    "explain": "low",
    "howto": "low",
    "doc_review": "low",
    "code_gen": "high",
    "refactor": "high",
    "debug": "high",
}

#: 层级 → completion 单价上限（$/1M tokens）；high 不设限
_DEFAULT_TIERS: Dict[str, Dict[str, float]] = {
    "low": {"max_completion_price": 1.0},
    "high": {},
}


class CostRouter:
    def __init__(self, config: Dict[str, Any]):
        cr_cfg = config.get("cost_routing", {})
        self._enabled = bool(cr_cfg.get("enabled", False))
        self._tiers: Dict[str, Dict[str, float]] = {**_DEFAULT_TIERS, **(cr_cfg.get("tiers") or {})}
        self._intent_tier: Dict[str, str] = {**_DEFAULT_INTENT_TIER, **(cr_cfg.get("intent_tier") or {})}
        self._prices = load_model_prices()
        self._default_model = str(config.get("model", {}).get("default", "gpt-4o-mini"))

    @property
    def enabled(self) -> bool:
        return self._enabled

    def route(self, intent: Optional[str], candidates: Optional[List[str]] = None) -> str:
        """在意图价格层级内选最便宜的已定价模型；未开启或无候选时返回默认模型"""
        if not self._enabled:
            return self._default_model
        tier_name = self._intent_tier.get(intent or "", "high")
        ceiling = self._tiers.get(tier_name, {}).get("max_completion_price")

        pool = list(candidates) if candidates else [m for m in self._prices if not m.startswith("mock")]
        priced = [
            m for m in pool
            if m in self._prices and (ceiling is None or self._prices[m].get("completion", 0.0) <= ceiling)
        ]
        if not priced:
            logger.info("成本路由：层级 %s 无符合价格上限的模型，回落默认 %s", tier_name, self._default_model)
            return self._default_model
        # cheapest-in-tier：按 completion 单价升序（同价按 prompt 单价）
        chosen = min(priced, key=lambda m: (self._prices[m].get("completion", 0.0),
                                            self._prices[m].get("prompt", 0.0)))
        if chosen != self._default_model:
            logger.info("成本路由：intent=%s 层级=%s → %s", intent, tier_name, chosen)
        return chosen
