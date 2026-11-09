"""rsi doctor：迁移/协作健康检查——记忆是否随 git 走。

用户真实反馈：项目迁移时只有 AGENTS.md 不够用，.rsi/ 被整体忽略导致记忆断层。
本命令检查 .rsi/memory 的 git 追踪状态，--fix 一键改写为细粒度忽略规则。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from .bootstrap_command import ensure_gitignore


def _git(project_root: Path, *git_args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *git_args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.returncode, (proc.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def check_memory_tracking(project_root: Path) -> Dict[str, Any]:
    """检查 .rsi/memory 是否随 git 走。纯读操作，不写任何文件。"""
    root = Path(project_root)
    memory = root / ".rsi" / "memory"
    report: Dict[str, Any] = {
        "memory_exists": memory.is_dir(),
        "local_files": 0,
        "git_repo": False,
        "ignored": None,
        "tracked_files": 0,
        "ok": False,
        "problems": [],
        "hints": [],
    }
    if report["memory_exists"]:
        report["local_files"] = sum(1 for p in memory.rglob("*") if p.is_file())
    else:
        report["problems"].append(".rsi/memory 不存在：尚未学习，先跑 rsi bootstrap")

    rc, _ = _git(root, "rev-parse", "--is-inside-work-tree")
    report["git_repo"] = rc == 0
    if not report["git_repo"]:
        report["problems"].append("不是 git 仓库：记忆无法随克隆/迁移走")
        report["hints"].append("git init 并提交；或用 rsi bundle export 做文件级迁移")
        return report

    # 探目录内的文件路径而非目录本身：git 对尾斜杠目录路径的 check-ignore 判定不可靠
    rc, _ = _git(root, "check-ignore", "-q", ".rsi/memory/.doctor-probe")
    report["ignored"] = rc == 0
    rc, out = _git(root, "ls-files", ".rsi/memory")
    tracked = [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else []
    report["tracked_files"] = len(tracked)

    if report["ignored"]:
        report["problems"].append(".rsi/memory 被 .gitignore 忽略：协作/迁移会丢记忆")
        report["hints"].append("rsi doctor --fix 一键改写为细粒度规则")
    elif report["local_files"] and not tracked:
        report["problems"].append(".rsi/memory 尚未提交到 git")
        report["hints"].append("git add .rsi/memory && git commit")

    report["ok"] = bool(
        report["memory_exists"]
        and report["git_repo"]
        and not report["ignored"]
        and (not report["local_files"] or tracked)
    )
    return report


def run_doctor(args: Any) -> int:
    project_root = Path(getattr(args, "project_root", None) or ".").resolve()
    if getattr(args, "fix", False):
        changed = ensure_gitignore(project_root)
        if changed:
            print("已改写 .gitignore：只忽略本地产物，.rsi/memory 随 git 走")
        else:
            print(".gitignore 无需改动")

    report = check_memory_tracking(project_root)
    mark = {True: "✓", False: "✗", None: "-"}
    print(f"项目: {project_root}")
    print(f"  {mark[report['memory_exists']]} .rsi/memory 存在"
          f"（{report['local_files']} 个文件）")
    print(f"  {mark[report['git_repo']]} git 仓库")
    if report["git_repo"]:
        ignored = report["ignored"]
        if ignored:
            print("  ✗ .rsi/memory 仍被 .gitignore 忽略")
        else:
            print("  ✓ .rsi/memory 未被忽略")
        print(f"  {mark[bool(report['tracked_files']) or not report['local_files']]} "
              f"git 已追踪 {report['tracked_files']} 个记忆文件")

    if report["ok"]:
        print("结论: 记忆随 git 走，克隆/迁移不会断层")
        return 0
    print("结论: 存在记忆断层风险")
    for problem in report["problems"]:
        print(f"  问题: {problem}")
    for hint in report["hints"]:
        print(f"  修复: {hint}")
    return 1
