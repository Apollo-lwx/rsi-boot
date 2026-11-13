"""注入黑名单（Spec v3.0 §4.2 第三道闸）：规则文件内容属提示词注入面。

模式由包内 config/injection_blocklist.yaml 维护；命中即拒绝注入该条目（记 warning）。
与 §8.1 脱敏、§4.6 审批闸门共同构成注入面三道闸。

fail-closed 约定（任务 0.4，借鉴 OpenMausBot safety floor「读不懂≠放行」）：
- 清单文件读不出/解析失败 = 检查器自身失败 → is_blocked 一律返回 True（拒绝），
  记 error。宁可全部不注入，不在黑名单状态未知时放行。
- 单条正则编译失败 = 跳过该条 + 记 error，其余模式照常生效。
"""

from __future__ import annotations

import logging
import re
from importlib import resources
from typing import List

import yaml

logger = logging.getLogger(__name__)

_patterns: List[re.Pattern] | None = None
_load_failed: bool = False


def _load() -> List[re.Pattern]:
    global _patterns, _load_failed
    if _patterns is None:
        try:
            text = (resources.files("rsi_boot") / "config" / "injection_blocklist.yaml").read_text(encoding="utf-8")
            raw = (yaml.safe_load(text) or {}).get("patterns") or []
        except Exception as exc:
            _load_failed = True
            logger.error("注入黑名单加载失败，按全拒绝处理: %s", exc)
            _patterns = []
            return _patterns
        compiled: List[re.Pattern] = []
        for p in raw:
            try:
                compiled.append(re.compile(p, re.IGNORECASE))
            except re.error as exc:
                logger.error("注入黑名单模式 %r 编译失败，跳过该条: %s", p, exc)
        _patterns = compiled
    return _patterns


def is_blocked(text: str) -> bool:
    """文本命中任一黑名单模式返回 True；清单加载失败（状态未知）返回 True"""
    if _load_failed:
        return True
    patterns = _load()
    if _load_failed:
        return True
    return any(p.search(text) for p in patterns)
