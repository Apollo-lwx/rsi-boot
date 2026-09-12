"""信号发现引擎（§10.9.2/§10.9.6）：检测 9 类信号源是否存在并生成扫描计划。

核心层（P1.17）采集 docs/config/conventions；code/git/conversation 的
深度采集在 P2.6 落地，本层仅发现与计数（供扫描计划与报告展示）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional

# 不扫描的目录（§10.9.6 设计原则：二进制/编译产物/依赖/缓存目录除外）
EXCLUDED_DIRS = frozenset({
    ".git", "node_modules", "vendor", "__pycache__", ".venv", "venv",
    "dist", "build", ".idea", ".mypy_cache", ".pytest_cache", "target",
    ".vite-cache", ".cache", ".turbo", ".next", ".nuxt", "coverage", ".parcel-cache",
})

_DOC_NAMES = {"readme", "contributing", "changelog", "history", "authors"}
_DOC_EXTS = {".md", ".rst", ".txt", ".adoc"}
_CODE_EXTS = {".py", ".js", ".ts", ".vue", ".java", ".go", ".rs", ".cs", ".cpp"}
_CONFIG_NAMES = {
    "pyproject.toml", "package.json", "pom.xml", "cargo.toml", "go.mod",
    "makefile", "dockerfile", "docker-compose.yml", "docker-compose.yaml",
}
_CONVENTION_NAMES = {".editorconfig", ".eslintrc", ".prettierrc", "tsconfig.json"}


@dataclass
class SignalInfo:
    """单类信号源的发现结果"""
    kind: str                       # docs/code/git/conversation/config/tests/conventions/templates/ci
    present: bool
    files: List[Path] = field(default_factory=list)
    note: str = ""

    @property
    def file_count(self) -> int:
        return len(self.files)


def _walk_files(root: Path, max_file_size: int) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                if path.stat().st_size > max_file_size:
                    continue
            except OSError:
                continue
            yield path


def discover_signals(
    project_root: Path,
    max_file_size: int = 1_000_000,
    on_progress: Optional[Callable[[int], None]] = None,
) -> Dict[str, SignalInfo]:
    """扫描项目目录树，返回 9 类信号的发现结果"""
    root = Path(project_root)
    signals: Dict[str, SignalInfo] = {k: SignalInfo(k, False) for k in (
        "docs", "code", "git", "conversation", "config", "tests", "conventions", "templates", "ci",
    )}

    walked = 0
    for path in _walk_files(root, max_file_size):
        walked += 1
        if on_progress is not None:
            on_progress(walked)
        rel = path.relative_to(root)
        parts = {p.lower() for p in rel.parts[:-1]}
        name = path.name.lower()
        stem = path.stem.lower()
        ext = path.suffix.lower()

        if ext in _DOC_EXTS or stem in _DOC_NAMES or "docs" in parts or "wiki" in parts:
            signals["docs"].files.append(path)
        if ext in _CODE_EXTS:
            signals["code"].files.append(path)
        if name in _CONFIG_NAMES or ext in {".toml", ".ini", ".cfg"} or (
            name.startswith("requirements") and ext == ".txt"
        ):
            signals["config"].files.append(path)
        if name in _CONVENTION_NAMES or name.startswith((".eslintrc", ".prettierrc", "tsconfig")):
            signals["conventions"].files.append(path)
        if name.startswith("test_") or name.endswith((".test.js", ".spec.ts", "_test.go")) or "tests" in parts or "__tests__" in parts:
            signals["tests"].files.append(path)
        if ".github" in parts and ("workflows" in parts or "issue_template" in parts or "pull_request_template" in parts):
            (signals["ci"] if "workflows" in parts else signals["templates"]).files.append(path)
        if name.startswith(("jenkinsfile", ".gitlab-ci")) or ".circleci" in parts:
            signals["ci"].files.append(path)
        if ".cursor" in parts or ".vscode" in parts or stem in {"changelog", "history", "release_notes"}:
            signals["conversation"].files.append(path)

    git_dir = root / ".git"
    signals["git"].present = git_dir.is_dir()
    if signals["git"].present:
        signals["git"].note = ".git 存在（深度分析在 P2.6）"

    for info in signals.values():
        info.present = info.present or bool(info.files)
    return signals


def plan_scopes(signals: Dict[str, SignalInfo], scope: str, consent: bool) -> Dict[str, bool]:
    """按 --scope 与 consent 生成各维度是否采集的执行计划（§10.9.1 同意机制）"""
    all_scopes = {"docs", "code", "git", "conversation", "config"}
    selected = set(scope.split(",")) & all_scopes if scope else all_scopes
    plan: Dict[str, bool] = {}
    for name in sorted(all_scopes):
        if name not in selected:
            plan[name] = False
        elif name == "conversation" and not consent:
            plan[name] = False  # 未显式同意，跳过对话维度
        else:
            plan[name] = True
    return plan
