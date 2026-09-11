"""零 Key 静态校验门禁（Spec v3.0 §4.6）：提案晋升前的确定性检查。

最小化五项：单槽位 / 单目标 / diff ≤ 50 行 / 绑定失败桶 / 含 before-after，
外加脱敏扫描与注入黑名单过滤（§4.2 三道闸在提案路径的落点）。任一不过即 rejected。
回放回归门禁需生成能力，为附录 C 可选增强（enhance.gate_replay）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from ..core.masking import mask_text
from ..injector.blocklist import is_blocked
from .regression_gate import RegressionReport

logger = logging.getLogger(__name__)

MAX_DIFF_LINES = 50          # 单提案变更行数上限
WRITABLE_SLOTS = ("prompt_template", "skill", "strategy", "intent_rule", "knowledge")


class StaticGate:
    """与 RegressionGate 同接口（evaluate → RegressionReport），零 LLM 零 embedding"""

    async def evaluate(self, proposal: Dict[str, Any]) -> RegressionReport:
        slot = str(proposal.get("slot") or "")
        report = RegressionReport(
            slot=slot, samples=0, target_metric="static_checks",
            baseline_target=0.0, candidate_target=0.0,
        )
        failures = self._check(proposal)
        report.passed = not failures
        report.reason = "静态校验通过（待人工确认）" if not failures else "；".join(failures)
        report.dimensions = {"static_checks": {"baseline": 0.0, "candidate": float(len(failures))}}
        return report

    def _check(self, proposal: Dict[str, Any]) -> List[str]:
        failures: List[str] = []
        slot = str(proposal.get("slot") or "")
        target_ref = str(proposal.get("target_ref") or "")
        payload = proposal.get("payload") or {}
        after = payload.get("after")
        evidence = proposal.get("evidence") or {}

        # ① 单槽位：槽位必须在可写面内
        if slot not in WRITABLE_SLOTS:
            failures.append(f"槽位 {slot!r} 不在可写面 {WRITABLE_SLOTS}")
        # ② 单目标：target_ref 必填且不含多目标分隔符
        if not target_ref or "," in target_ref or ";" in target_ref:
            failures.append("目标缺失或含多目标")
        # ③ diff ≤ 50 行（以 after 内容行数计）
        if after is not None:
            diff_lines = len(json.dumps(after, ensure_ascii=False, indent=1).splitlines())
            if diff_lines > MAX_DIFF_LINES:
                failures.append(f"变更 {diff_lines} 行超上限 {MAX_DIFF_LINES}")
        # ④ 绑定失败桶证据
        bucket = evidence.get("bucket") or {}
        if not bucket.get("count") or not evidence.get("log_ids"):
            failures.append("未绑定失败桶证据")
        # ⑤ 含 before/after（add 动作 before 可为空）
        if "after" not in payload and "before" not in payload:
            failures.append("payload 缺少 before/after")

        # 脱敏扫描：after 文本若含未脱敏密钥/敏感串（mask 后有变化）即否决
        after_text = json.dumps(after, ensure_ascii=False) if after is not None else ""
        if after_text and mask_text(after_text) != after_text:
            failures.append("after 含未脱敏敏感信息")
        # 注入黑名单：提案内容将进入宿主上下文，命中元指令模式即否决
        if after_text and is_blocked(after_text):
            failures.append("after 命中注入黑名单")
        return failures
