"""Git 历史分析（§10.9.2/§3.8，P2.6）：commit 风格 / 贡献领域 / 文件归属。

`git log --all` 统计分析（subprocess，只读操作）。Git 不可用/非仓库 → 返回 None，
跳过该维度不影响其他（§10.9.9）。拉取中断重试 3 次（指数退避）。
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_CONVENTIONAL_RE = re.compile(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\(.+?\))?!?:\s")
_COMMIT_TYPES = ("feat", "fix", "docs", "refactor", "test", "chore", "perf", "ci", "build", "style")

#: §3.8：conventional 匹配率 ≥ 70% 判定；文件归属占比 ≥ 20% → 贡献领域
CONVENTIONAL_THRESHOLD = 0.7
OWNERSHIP_THRESHOLD = 0.2


@dataclass
class GitInsights:
    total_commits: int = 0
    authors: List[str] = field(default_factory=list)
    conventional_ratio: float = 0.0
    commit_types: Dict[str, int] = field(default_factory=dict)
    top_files: List[str] = field(default_factory=list)          # 变更最频繁的文件
    ownership: Dict[str, str] = field(default_factory=dict)     # 文件 -> 主要作者 email
    subjects: List[str] = field(default_factory=list)           # commit subject（画像摘要输入）

    @property
    def commit_style(self) -> Optional[str]:
        """匹配率 ≥70% 判定 conventional，否则留空（§3.8：低于阈值宁可留空）"""
        if not self.total_commits:
            return None
        return "conventional" if self.conventional_ratio >= CONVENTIONAL_THRESHOLD else None

    def owned_modules(self, user_email: str) -> List[str]:
        """当前用户行数/提交占比 ≥20% 的模块（顶层目录）→ 贡献领域标签"""
        if not user_email:
            return []
        counter: Counter = Counter()
        totals: Counter = Counter()
        file_author: Dict[str, str] = self.ownership
        for f, author in file_author.items():
            top = f.replace("\\", "/").split("/")[0] if "/" in f.replace("\\", "/") else "."
            totals[top] += 1
            if author == user_email:
                counter[top] += 1
        return [
            mod for mod, cnt in counter.items()
            if totals[mod] and cnt / totals[mod] >= OWNERSHIP_THRESHOLD
        ]

    def to_summary(self) -> str:
        lines = [f"Git 历史：{self.total_commits} commits，{len(self.authors)} 位贡献者"]
        if self.authors:
            lines.append(f"主要贡献者：{', '.join(self.authors[:5])}")
        if self.commit_types:
            top = sorted(self.commit_types.items(), key=lambda kv: -kv[1])[:5]
            lines.append("提交类型分布：" + ", ".join(f"{k}({v})" for k, v in top))
        lines.append(f"conventional commits 匹配率：{self.conventional_ratio:.0%}")
        if self.top_files:
            lines.append(f"热点文件：{', '.join(self.top_files[:8])}")
        return "\n".join(lines)


def _run_git(project_root: Path, args: List[str], retries: int = 3) -> Optional[str]:
    """git 子进程（只读）；失败指数退避重试 3 次，最终失败返回 None（§10.9.9）"""
    for attempt in range(retries):
        try:
            out = subprocess.run(
                ["git", *args],
                cwd=project_root, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
            if out.returncode == 0:
                return out.stdout
            raise subprocess.CalledProcessError(out.returncode, out.args, out.stdout, out.stderr)
        except (OSError, subprocess.SubprocessError) as exc:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                logger.warning("git %s 失败（已重试 %d 次）: %s", args[0], retries, exc)
    return None


def count_commits(project_root: Path) -> Optional[int]:
    """仓库提交总数；非仓库/git 失败返回 None。"""
    if not (Path(project_root) / ".git").is_dir():
        return None
    out = _run_git(Path(project_root), ["rev-list", "--all", "--count"])
    if out is None:
        return None
    try:
        return int(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def analyze_git(project_root: Path, max_commits: int = 500) -> Optional[GitInsights]:
    """分析 Git 历史；非仓库/git 不可用返回 None"""
    if not (project_root / ".git").is_dir():
        return None

    # 单次遍历同时取 author/email/subject：%an <%ae> 分隔符解析
    log_out = _run_git(project_root, [
        "log", "--all", f"-n{max_commits}", "--format=%an <%ae>|%s",
    ])
    if log_out is None:
        return None

    insights = GitInsights()
    authors: Counter = Counter()
    conventional = 0
    type_counter: Counter = Counter()
    for line in log_out.splitlines():
        if "|" not in line:
            continue
        author, subject = line.split("|", 1)
        author = author.strip()
        subject = subject.strip()
        if not subject:
            continue
        insights.total_commits += 1
        if author:
            authors[author] += 1
        if len(insights.subjects) < 50:
            insights.subjects.append(subject)
        match = _CONVENTIONAL_RE.match(subject)
        if match:
            conventional += 1
            type_counter[match.group(1)] += 1

    if insights.total_commits == 0:
        return insights
    insights.conventional_ratio = conventional / insights.total_commits
    insights.commit_types = dict(type_counter)
    insights.authors = [a for a, _ in authors.most_common(10)]

    # 文件变更频率 + 归属（--name-only + 作者邮箱）
    name_out = _run_git(project_root, [
        "log", "--all", f"-n{min(max_commits, 200)}", "--format=commit-author:%ae", "--name-only",
    ])
    if name_out:
        file_counter: Counter = Counter()
        file_authors: Dict[str, Counter] = defaultdict(Counter)
        current_author = ""
        for line in name_out.splitlines():
            line = line.strip()
            if line.startswith("commit-author:"):
                current_author = line.split(":", 1)[1]
            elif line and not line.startswith("commit "):
                file_counter[line] += 1
                if current_author:
                    file_authors[line][current_author] += 1
        insights.top_files = [f for f, _ in file_counter.most_common(20)]
        insights.ownership = {
            f: counter.most_common(1)[0][0] for f, counter in file_authors.items() if counter
        }
    return insights
