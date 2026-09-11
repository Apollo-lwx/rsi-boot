"""数据脱敏（Spec §8.1，P1.10 完整版）。

MASKING_RULES 是日志脱敏与 §10.9.7 敏感信息扫描的单一事实来源（10 条规则表）。
执行时机（双扫描）：Log Handler 写库前对 raw_input/响应内容全表扫描；
外发 LLM 的 prompt 组装后同样过一遍（防知识库内容带密钥外发）。

可配置扩展（config 的 masking 节）：
- extra_rules: [{name, pattern, replacement}] 追加自定义规则
- disabled_rules: [name] 按名禁用内置规则
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional


@dataclass(frozen=True)
class MaskingRule:
    name: str
    pattern: re.Pattern[str]
    replacement: str


# §8.1 脱敏正则模式表（单一事实来源，scanner 敏感信息检测复用）
# 执行顺序说明：internal_ip 必须先于 ipv4，否则内网地址会被 ipv4 规则提前改写
MASKING_RULES: List[MaskingRule] = [
    MaskingRule("email", re.compile(r"\b[\w.+-]+@(([\w-]+\.)+[\w-]{2,})\b"), r"***@\1"),
    MaskingRule("internal_ip", re.compile(r"\b(10\.\d{1,3}|172\.(1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b"), "[INTERNAL_IP]"),
    MaskingRule("ipv4", re.compile(r"\b(\d{1,3}\.\d{1,3})\.\d{1,3}\.\d{1,3}\b"), r"\1.*.*"),
    MaskingRule("openai_key", re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "sk-****"),
    MaskingRule("aws_akid", re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA****"),
    MaskingRule("github_token", re.compile(r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}"), "gh*_****"),
    MaskingRule("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "xox*-****"),
    MaskingRule("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "eyJ****"),
    MaskingRule("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[PRIVATE KEY REDACTED]"),
    MaskingRule("password_field", re.compile(r"(?i)(password|passwd|pwd|secret)\s*[:=]\s*[\"']?[^\s\"']{4,}"), r"\1=****"),
]

# 默认规则集（无配置时使用）；configure() 后指向定制规则集
_active_rules: List[MaskingRule] = MASKING_RULES


def configure(config: Optional[dict[str, Any]] = None) -> None:
    """按配置定制规则集：disabled_rules 禁用、extra_rules 追加（§8.1 可配置规则）"""
    global _active_rules
    masking_cfg = (config or {}).get("masking", {})
    disabled = set(masking_cfg.get("disabled_rules", []))
    rules = [r for r in MASKING_RULES if r.name not in disabled]
    for extra in masking_cfg.get("extra_rules", []):
        rules.append(MaskingRule(extra["name"], re.compile(extra["pattern"]), extra["replacement"]))
    _active_rules = rules


def active_rules() -> List[MaskingRule]:
    return _active_rules


def mask_text(text: str, rules: Optional[Iterable[MaskingRule]] = None) -> str:
    """对文本执行全表扫描脱敏（写库前与外发 LLM 前双时机）"""
    if not text:
        return text
    masked = text
    for rule in rules if rules is not None else _active_rules:
        masked = rule.pattern.sub(rule.replacement, masked)
    return masked


def mask_messages(messages: List[dict[str, str]]) -> List[dict[str, str]]:
    """外发 LLM 前对组装后的 messages 脱敏（防知识库内容带密钥外发）"""
    return [{**m, "content": mask_text(m.get("content", ""))} for m in messages]
