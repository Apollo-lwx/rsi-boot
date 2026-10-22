"""固定参数列表启动子进程。shell 始终关闭，参数里不允许 NUL。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional, Sequence

_FORMAT_RE = re.compile(r"^--format=[%A-Za-z0-9 <>|:.\\_-]+$")
_LOG_FLAGS = {"--all", "--name-only"}


def run_argv(
    argv: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    args = [str(part) for part in argv]
    if not args or any("\x00" in part for part in args):
        raise ValueError("invalid argv")
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        check=False,
    )


def git_args_allowed(args: Sequence[str]) -> bool:
    """只允许只读的 config user.email、rev-list --count 和受限的 git log。"""
    parts = [str(part) for part in args]
    if parts == ["config", "user.email"]:
        return True
    if parts == ["rev-list", "--all", "--count"]:
        return True
    if not parts or parts[0] != "log":
        return False
    saw_limit = False
    for token in parts[1:]:
        if token in _LOG_FLAGS:
            continue
        if token.startswith("--format="):
            if _FORMAT_RE.fullmatch(token) is None:
                return False
            continue
        if token.startswith("-n") and token[2:].isdigit():
            limit = int(token[2:])
            if saw_limit or not 1 <= limit <= 10000:
                return False
            saw_limit = True
            continue
        return False
    return saw_limit


def run_git(
    cwd: Path,
    args: Sequence[str],
    *,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    if not git_args_allowed(args):
        raise ValueError("git argv not allowed")
    return run_argv(["git", *[str(part) for part in args]], cwd=cwd, timeout=timeout)
