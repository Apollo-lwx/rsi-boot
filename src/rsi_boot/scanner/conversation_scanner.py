"""对话上下文扫描（§10.9.2/§10.9.6，P2.6）：历史决策与常见问题模式提取。

仅提取问题和模式，不导入个人信息（§10.9.6 设计原则）。
需用户显式同意（--consent），未同意时整个维度不进入扫描计划（§10.9.7 隐私同意）。
来源：CHANGELOG*/HISTORY*/RELEASE_NOTES*（决策记录）+ .cursor/.vscode 下的
Markdown 笔记（问答模式）。.json 类 IDE 状态文件不解析（噪声高、隐私风险）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .validator import read_text_tolerant

logger = logging.getLogger(__name__)

# 决策信号词：架构决策/选型讨论是最有价值的知识之一（§10.9.11 场景 D）
_DECISION_RE = re.compile(
    r"(决定|决策|选型|为什么选|最终采用|decision|decided|rationale|trade-?off|ADR)",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"^[^。\n]{5,120}[?？]\s*$", re.MULTILINE)
_MAX_PATTERNS_PER_FILE = 10
_MAX_PATTERN_CHARS = 600


@dataclass
class ConversationPattern:
    """提取的对话/决策模式"""

    source: str           # 相对路径
    kind: str             # decision | question
    text: str


def _extract_from_text(text: str, rel: str) -> List[ConversationPattern]:
    patterns: List[ConversationPattern] = []

    # 决策段落：命中决策词的段落（按空行分段）
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if para and _DECISION_RE.search(para):
            patterns.append(ConversationPattern(source=rel, kind="decision", text=para[:_MAX_PATTERN_CHARS]))

    # 常见问题：独立成行的问句
    for match in _QUESTION_RE.finditer(text):
        patterns.append(ConversationPattern(source=rel, kind="question", text=match.group(0).strip()))

    return patterns[:_MAX_PATTERNS_PER_FILE]


def scan_conversations(project_root: Path, files: List[Path]) -> List[ConversationPattern]:
    """批量提取对话模式；单文件失败跳过（§10.9.9）"""
    patterns: List[ConversationPattern] = []
    for path in files:
        if path.suffix.lower() not in {".md", ".txt", ".rst"}:
            continue  # IDE .json 状态文件不解析（噪声/隐私）
        rel = str(path.relative_to(project_root))
        try:
            text = read_text_tolerant(path)
            if text:
                patterns.extend(_extract_from_text(text, rel))
        except Exception as exc:
            logger.info("对话模式提取失败，跳过 %s: %s", rel, exc)
    return patterns
