"""跨信号源关联推理（§10.9.8，P2.6）：自学习区别于简单导入的核心能力。

关联方式：
- 命名关联：test_x.py ↔ x.py（测试 ↔ 代码）
- 文本关联：文档标题词集与模块名 Jaccard 相似度 ≥ 0.5（文档 ↔ 代码）
- Commit ↔ 文件：git 归属统计（git_analyzer 产出，此处汇总为关联对计数）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

from .code_scanner import ModuleSkeleton
from .git_analyzer import GitInsights

_JACCARD_THRESHOLD = 0.5
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


@dataclass
class Correlation:
    """一对跨信号关联"""

    kind: str        # test_code | doc_code | commit_file
    source: str      # 如 test_user.py
    target: str      # 如 user.py
    confidence: float

    def to_text(self) -> str:
        label = {"test_code": "测试覆盖", "doc_code": "文档说明", "commit_file": "提交归属"}[self.kind]
        return f"{label}：{self.source} ↔ {self.target}（置信度 {self.confidence:.2f}）"


def _words(text: str) -> Set[str]:
    return {w.lower() for w in _WORD_RE.findall(text) if len(w) > 1}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def correlate_test_code(test_files: List[Path], code_files: List[Path], project_root: Path) -> List[Correlation]:
    """命名关联：test_user.py ↔ user.py / user_test.go ↔ user.go"""
    code_by_stem: Dict[str, str] = {}
    for path in code_files:
        rel = str(path.relative_to(project_root)).replace("\\", "/")
        code_by_stem.setdefault(path.stem.lower(), rel)

    pairs: List[Correlation] = []
    for path in test_files:
        stem = path.stem.lower()
        for prefix in ("test_",):
            if stem.startswith(prefix):
                stem = stem[len(prefix):]
        for suffix in ("_test", ".test", ".spec"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
        target = code_by_stem.get(stem)
        if target:
            pairs.append(Correlation(
                kind="test_code",
                source=str(path.relative_to(project_root)).replace("\\", "/"),
                target=target,
                confidence=0.9,
            ))
    return pairs


def correlate_doc_code(doc_titles: Dict[str, str], skeletons: List[ModuleSkeleton]) -> List[Correlation]:
    """文本关联：文档标题/首行词集 ↔ 模块文件名stem 词集，Jaccard ≥ 0.5"""
    pairs: List[Correlation] = []
    module_words: List[tuple[str, Set[str]]] = []
    for s in skeletons:
        stem_words = _words(Path(s.rel_path).stem.replace("_", " ").replace("-", " "))
        if stem_words:
            module_words.append((s.rel_path, stem_words))

    for doc_rel, title in doc_titles.items():
        title_words = _words(title)
        if not title_words:
            continue
        best: Optional[tuple[str, float]] = None
        for rel, words in module_words:
            sim = _jaccard(title_words, words)
            if sim >= _JACCARD_THRESHOLD and (best is None or sim > best[1]):
                best = (rel, sim)
        if best:
            pairs.append(Correlation(kind="doc_code", source=doc_rel, target=best[0], confidence=best[1]))
    return pairs


def correlate_commit_files(git: Optional[GitInsights], limit: int = 20) -> List[Correlation]:
    """Commit ↔ 文件：归属统计转关联对（谁主要负责哪块代码）"""
    if git is None:
        return []
    return [
        Correlation(kind="commit_file", source=author, target=rel, confidence=0.8)
        for rel, author in list(git.ownership.items())[:limit]
    ]


def summarize_correlations(pairs: List[Correlation]) -> str:
    """关联成果摘要文本（写入知识库 + 报告计数）"""
    by_kind: Dict[str, int] = {}
    for p in pairs:
        by_kind[p.kind] = by_kind.get(p.kind, 0) + 1
    lines = [f"跨信号关联共 {len(pairs)} 对：" + ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items()))]
    lines.extend(p.to_text() for p in pairs[:30])
    return "\n".join(lines)
