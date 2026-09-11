"""注入黑名单（Spec v3.0 §4.2 第三道闸）：规则文件内容属提示词注入面。

模式由包内 config/injection_blocklist.yaml 维护；命中即拒绝注入该条目（记 warning）。
与 §8.1 脱敏、§4.6 审批闸门共同构成注入面三道闸。
"""

from __future__ import annotations

import logging
import re
from importlib import resources
from typing import List

import yaml

logger = logging.getLogger(__name__)

_patterns: List[re.Pattern] | None = None


def _load() -> List[re.Pattern]:
    global _patterns
    if _patterns is None:
        text = (resources.files("rsi_boot") / "config" / "injection_blocklist.yaml").read_text(encoding="utf-8")
        raw = (yaml.safe_load(text) or {}).get("patterns") or []
        _patterns = [re.compile(p, re.IGNORECASE) for p in raw]
    return _patterns


def is_blocked(text: str) -> bool:
    """文本命中任一黑名单模式返回 True"""
    return any(p.search(text) for p in _load())
