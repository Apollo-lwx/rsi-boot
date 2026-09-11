"""用户规则文件禁止句式提取（ISSUE-6）：bootstrap 冷启动 prohibition 种子。

扫描 .cursor/rules/*.mdc（非 rsi- 前缀，rsi- 为自产文件）、.cursorrules、
AGENTS.md（托管块除外）、CLAUDE.md、.github/copilot-instructions.md，
按禁止句式规则式提取，产出 pending_review 草稿。
与 §4.9 冲突检测互补：种子来自用户手写规则，方向天然一致，不会误报冲突。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from ..core.masking import mask_text
from ..injector.targets import AgentsMdTarget, discover_user_rule_files

logger = logging.getLogger(__name__)

_PROHIBITION_RE = re.compile(
    r"(禁止|严禁|不要|不得|避免|一律不|不允许|不许|"
    r"never|don['’]?t|do\s+not|must\s+not|avoid)",
    re.IGNORECASE,
)
_LIST_PREFIX_RE = re.compile(r"^(?:[-*+>]|\d+[.)]|\s)+")
_MANAGED_BLOCK_RE = re.compile(
    re.escape(AgentsMdTarget.BEGIN) + r".*?" + re.escape(AgentsMdTarget.END), re.DOTALL
)

_MIN_LINE_CHARS = 6     # 过短（如「短禁止」）无信息量
_MAX_LINE_CHARS = 200   # 超长行多为段落而非规则句
_MAX_SEEDS_PER_FILE = 20


@dataclass
class RuleSeed:
    title: str
    content: str
    source: str  # 相对路径#行号


def extract_prohibition_lines(text: str) -> List[str]:
    """从规则文本提取禁止句式行（去 markdown 列表/标题前缀，文件内去重）"""
    items: List[str] = []
    for raw in text.splitlines():
        line = _LIST_PREFIX_RE.sub("", raw.strip()).lstrip("#").strip()
        # 代码注释（// /* *）与前导引号/反引号不进种子标题
        line = line.lstrip("/*\"'`").strip()
        if len(line) < _MIN_LINE_CHARS or len(line) > _MAX_LINE_CHARS:
            continue
        if _PROHIBITION_RE.search(line) and line not in items:
            items.append(line)
    return items


def _rule_file_candidates(project_root: Path) -> List[Path]:
    return discover_user_rule_files(project_root)


def scan_rule_seeds(project_root: Path) -> List[RuleSeed]:
    """收集用户规则文件中的禁止句式种子；单文件失败跳过"""
    root = Path(project_root)
    seeds: List[RuleSeed] = []
    for path in _rule_file_candidates(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.info("规则文件读取失败，跳过 %s: %s", path, exc)
            continue
        if path.name == "AGENTS.md":
            text = _MANAGED_BLOCK_RE.sub("", text)  # 托管块是自产内容，不作种子
        rel = path.relative_to(root).as_posix()
        for line in extract_prohibition_lines(text)[:_MAX_SEEDS_PER_FILE]:
            lineno = text[: text.find(line)].count("\n") + 1 if line in text else 0
            content = mask_text(line)
            seeds.append(RuleSeed(
                title=content[:40], content=content, source=f"{rel}#L{lineno}",
            ))
    return seeds
