"""注入 footprint 计量（价值宣讲的成本侧）：RSI 每轮往宿主上下文里塞了多少字符/约多少 token。

对标 Qoder「记忆触达成本」可观测诉求。口径务必诚实：
- 字符数是**精确可测**的（直接读磁盘上 rsi-*.mdc 与 AGENTS.md 托管块）；
- token 是**估算**：宿主模型的真实分词器不可见，这里用「CJK 每字≈1 token、
  其余每 4 字符≈1 token」的通用启发式，标注为估算，不谎报精确值。

真正省了多少 token 只能靠「用前 vs 用后」对照（宿主自己报用量），本模块不算那部分，
只提供 ImpactService 需要的成本输入。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from rsi_boot.injector.targets import AgentsMdTarget

#: CJK 及全角标点区间（与 rag.index._CJK_RUN 口径一致，宽口径归入「每字≈1 token」）
_CJK = re.compile(r"[一-鿿　-〿＀-￯぀-ヿ]")


def estimate_tokens(text: str) -> int:
    """把字符数折算成 token 估算值：CJK 每字计 1，其余每 4 字符计 1（向上取整）。

    这是与宿主分词器无关的启发式，仅用于量级感知，须随结果标注「估算」。
    """
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


@dataclass
class FootprintArtifact:
    """单个注入载体的成本条目"""

    name: str          # 相对路径或载体名
    chars: int
    est_tokens: int

    def asdict(self) -> dict:
        return {"name": self.name, "chars": self.chars, "est_tokens": self.est_tokens}


@dataclass
class Footprint:
    """一次注入的全部载体成本合计"""

    artifacts: List[FootprintArtifact]

    @property
    def total_chars(self) -> int:
        return sum(a.chars for a in self.artifacts)

    @property
    def total_est_tokens(self) -> int:
        return sum(a.est_tokens for a in self.artifacts)

    def asdict(self) -> dict:
        return {
            "per_turn_chars": self.total_chars,
            "per_turn_est_tokens": self.total_est_tokens,
            "artifacts": [a.asdict() for a in self.artifacts],
        }


def _artifact(path: Path, name: str) -> Optional[FootprintArtifact]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text:
        return None
    return FootprintArtifact(name=name, chars=len(text), est_tokens=estimate_tokens(text))


def _agents_block(project_root: Path) -> Optional[FootprintArtifact]:
    """只取 AGENTS.md 里 BEGIN/END 之间的托管块（块外是用户自留地，不计入 RSI 成本）。"""
    path = Path(project_root) / "AGENTS.md"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    begin, end = AgentsMdTarget.BEGIN, AgentsMdTarget.END
    if begin not in text or end not in text:
        return None
    head, _, rest = text.partition(begin)
    block, _, _ = rest.partition(end)
    content = begin + block + end
    if not content.strip():
        return None
    return FootprintArtifact(name="AGENTS.md (托管块)", chars=len(content), est_tokens=estimate_tokens(content))


def measure_footprint(project_root: Path | str) -> Footprint:
    """从磁盘读取当前注入产物，计量 RSI 每轮被动注入的字符/token 成本。

    无产物（尚未 serve/bootstrap）时返回空 Footprint（合计 0），调用方据此判断「未启用」。
    """
    root = Path(project_root)
    artifacts: List[FootprintArtifact] = []
    rules_dir = root / ".cursor" / "rules"
    if rules_dir.is_dir():
        for path in sorted(rules_dir.glob("rsi-*.mdc")):
            one = _artifact(path, f".cursor/rules/{path.name}")
            if one is not None:
                artifacts.append(one)
    block = _agents_block(root)
    if block is not None:
        artifacts.append(block)
    return Footprint(artifacts=artifacts)
