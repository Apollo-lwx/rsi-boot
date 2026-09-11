"""技能槽运行时（§3.9）：config/skills/<name>/SKILL.md 文件型指令包。

技能 = 程序性记忆（"怎么做"），与 knowledge 的事实性记忆（"是什么"）互补。
frontmatter 声明 name/description/trigger（意图或关键词）；查询时按 trigger 匹配，
命中技能的指令体注入提示词，来源标注 `skill:<name>`，与知识注入共用 §3.2 预算。

发现路径（后者覆盖前者同名技能）：包内 config/skills/ → ~/.rsi/skills/ → 项目 .rsi/skills/。
目录扫描按 mtime 缓存，随配置热加载（§2.5）重建实例生效。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_MAX_SKILLS_PER_QUERY = 3      # 单次查询注入技能上限
_MAX_SKILL_CHARS = 2000        # 单技能指令体截断（与知识条目同尺度）


@dataclass
class Skill:
    name: str
    description: str
    body: str
    intents: List[str] = field(default_factory=list)   # trigger: 意图名
    keywords: List[str] = field(default_factory=list)  # trigger: 关键词（子串匹配，大小写不敏感）
    source_path: str = ""

    def matches(self, intent: Optional[str], raw_input: str) -> bool:
        if intent and intent in self.intents:
            return True
        lowered = raw_input.lower()
        return any(kw.lower() in lowered for kw in self.keywords if kw)


def parse_skill_md(text: str, source_path: str = "") -> Optional[Skill]:
    """解析 SKILL.md：YAML frontmatter（name/description/trigger）+ Markdown 指令体"""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    try:
        meta: Dict[str, Any] = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        logger.warning("技能 frontmatter 解析失败: %s", source_path)
        return None
    name = str(meta.get("name", "")).strip()
    if not name:
        return None
    trigger = meta.get("trigger") or {}
    if isinstance(trigger, list):  # 简写：trigger: [intent1, intent2]
        trigger = {"intents": trigger}
    body = text[match.end():].strip()
    return Skill(
        name=name,
        description=str(meta.get("description", "")),
        body=body[:_MAX_SKILL_CHARS],
        intents=[str(i) for i in trigger.get("intents") or []],
        keywords=[str(k) for k in trigger.get("keywords") or []],
        source_path=source_path,
    )


class SkillLoader:
    """技能发现与匹配：多目录扫描（后者覆盖同名），mtime 变化时重扫"""

    def __init__(self, dirs: List[Path]):
        self._dirs = [d for d in dirs if d.is_dir()]
        self._skills: Dict[str, Skill] = {}
        self._scan_signature: Optional[tuple] = None

    def _signature(self) -> tuple:
        sig = []
        for d in self._dirs:
            for path in sorted(d.glob("*/SKILL.md")):
                try:
                    sig.append((str(path), path.stat().st_mtime_ns))
                except OSError:
                    continue
        return tuple(sig)

    def refresh(self) -> int:
        """mtime 签名变化时重扫，返回技能数"""
        sig = self._signature()
        if sig == self._scan_signature:
            return len(self._skills)
        skills: Dict[str, Skill] = {}
        for d in self._dirs:
            for path in sorted(d.glob("*/SKILL.md")):
                try:
                    skill = parse_skill_md(path.read_text(encoding="utf-8"), str(path))
                except OSError:
                    continue
                if skill:
                    skills[skill.name] = skill  # 后目录覆盖同名
        self._skills = skills
        self._scan_signature = sig
        return len(skills)

    def match(self, intent: Optional[str], raw_input: str) -> List[Skill]:
        self.refresh()
        hits = [s for s in self._skills.values() if s.matches(intent, raw_input)]
        return hits[:_MAX_SKILLS_PER_QUERY]

    @property
    def skills(self) -> Dict[str, Skill]:
        self.refresh()
        return dict(self._skills)
