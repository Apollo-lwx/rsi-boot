"""Git fix 提交识别与列表（host-distill-knowledge Wave A）。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

_FIX_WORD = re.compile(
    r"(?:^|[^a-z0-9])(?:fix|hotfix|bug|revert)(?:[^a-z0-9]|$)|"
    r"^fix(\(.+?\))?!?:|"
    r"修复|缺陷|回滚",
    re.IGNORECASE,
)


def is_fix_subject(subject: str) -> bool:
    return bool(_FIX_WORD.search((subject or "").strip()))


def list_fix_commits(project_root: Path, max_commits: int = 500) -> list[dict[str, Any]]:
    """列出 fix 类提交；无 .git 或 git 失败时返回 []。"""
    root = Path(project_root)
    if not (root / ".git").is_dir():
        return []
    try:
        out = subprocess.run(
            [
                "git", "log", "--all", f"-n{max_commits}",
                "--name-only", "--format=%h%x00%s",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if out.returncode != 0:
            return []
    except (OSError, subprocess.SubprocessError):
        return []

    results: list[dict[str, Any]] = []
    current_hash = ""
    current_message = ""
    current_files: list[str] = []

    def _flush() -> None:
        if current_hash and is_fix_subject(current_message):
            results.append({
                "hash": current_hash,
                "message": current_message,
                "files": list(current_files),
            })

    for line in out.stdout.splitlines():
        if "\x00" in line:
            _flush()
            current_hash, current_message = line.split("\x00", 1)
            current_files = []
        elif line.strip():
            current_files.append(line.strip())
    _flush()
    return results
