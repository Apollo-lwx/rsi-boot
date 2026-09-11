"""数据验证（§10.9.7）：切片写入前的校验/去重/敏感信息检测。

敏感信息规则复用 §8.1 MASKING_RULES（单一事实来源）。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Set

from ..core.masking import MASKING_RULES, mask_text

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4
MIN_TOKENS = 50
MAX_TOKENS = 4000


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    content: str = ""           # 脱敏后的内容（允许写入时）
    risk: bool = False          # 命中敏感规则（--allow-sensitive 豁免时标记）


class StrictModeError(Exception):
    """--strict 模式下任何验证失败即抛出"""


@dataclass
class ErrorCollector:
    """错误收集器：默认模式记录并继续；strict 模式首个失败即中断（§10.9.7）"""

    strict: bool = False
    errors: List[str] = field(default_factory=list)

    def report(self, where: str, error: Exception) -> None:
        message = f"{where}: {error}"
        if self.strict:
            raise StrictModeError(message) from error
        logger.warning("跳过（%s）", message)
        self.errors.append(message)


def estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_chunk(content: str, allow_sensitive: bool = False) -> ValidationResult:
    """单条切片校验：信息量下限 → 敏感信息扫描（上限拆分在切片器完成）"""
    if estimate_tokens(content) < MIN_TOKENS:
        return ValidationResult(ok=False, reason=f"切片不足 {MIN_TOKENS} token（噪声跳过）")

    hit = any(rule.pattern.search(content) for rule in MASKING_RULES)
    if hit:
        if not allow_sensitive:
            return ValidationResult(ok=False, reason="命中敏感信息规则（--allow-sensitive 可豁免）")
        return ValidationResult(ok=True, content=mask_text(content), risk=True)
    return ValidationResult(ok=True, content=content)


def read_text_tolerant(path) -> Optional[str]:
    """编码检测：UTF-8 → GBK → Latin-1，全部失败返回 None（§10.9.7）"""
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        except OSError:
            return None
    return None


class DedupSet:
    """SHA256 去重：库内已有内容哈希 + 本次运行内哈希（§10.9.10 幂等性）"""

    def __init__(self, existing_hashes: Optional[Set[str]] = None):
        self._hashes: Set[str] = set(existing_hashes or set())

    def is_duplicate(self, content: str) -> bool:
        digest = content_hash(content)
        if digest in self._hashes:
            return True
        self._hashes.add(digest)
        return False
