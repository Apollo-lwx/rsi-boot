"""rsi wipe：一键清掉本项目库文件与指纹，保留 identity.json。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..project import project_rsi_dir, resolve_project_root

WIPE_NAMES = ("rsi.db", "rsi.db-wal", "rsi.db-shm", "manifest.json")
KEEP_NAMES = ("identity.json",)

# 只认 rsi 可执行文件 / rsi serve / 包模块，避免误杀路径里带 rsi-boot 的 Cursor/pytest
_HOLDER_RE = re.compile(
    r"(?:^|[\\/\s\"'])rsi(?:\.exe)?(?:\s|\"'|$)|\brsi_boot\b|\brsi serve\b",
    re.IGNORECASE,
)

_YES_HINT = (
    "清库会删除本项目 .rsi/rsi.db* 与 manifest.json，保留 identity.json。"
    "确认请加 --yes。"
)


def _norm(path: Path | str) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _is_holder_cmdline(cmdline: str, project_root: Path) -> bool:
    if not cmdline:
        return False
    root = _norm(project_root)
    hay = os.path.normcase(cmdline)
    if root not in hay:
        return False
    return bool(_HOLDER_RE.search(cmdline))


def _windows_process_rows() -> list[tuple[int, str]]:
    script = (
        "Get-CimInstance Win32_Process | "
        "ForEach-Object { '{0}\t{1}' -f $_.ProcessId, $_.CommandLine }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows: list[tuple[int, str]] = []
    for line in (completed.stdout or "").splitlines():
        pid_s, _, rest = line.partition("\t")
        try:
            rows.append((int(pid_s), rest or ""))
        except ValueError:
            continue
    return rows


def _posix_process_rows() -> list[tuple[int, str]]:
    try:
        completed = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows: list[tuple[int, str]] = []
    for line in (completed.stdout or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        try:
            rows.append((int(parts[0]), parts[1]))
        except ValueError:
            continue
    return rows


def _terminate_pid(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            completed = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return completed.returncode == 0
        os.kill(pid, 9)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def stop_db_holders(project_root: Path) -> list[int]:
    """结束命令行带本项目路径、且像 rsi serve 的进程。不含当前 wipe 进程。"""
    rows = _windows_process_rows() if sys.platform == "win32" else _posix_process_rows()
    mine = {os.getpid(), os.getppid()}
    stopped: list[int] = []
    for pid, cmdline in rows:
        if pid in mine or pid <= 0:
            continue
        if not _is_holder_cmdline(cmdline, project_root):
            continue
        if not _terminate_pid(pid):
            continue
        stopped.append(pid)
    return stopped


def _unlink(path: Path) -> str | None:
    try:
        path.unlink()
        return None
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"{path.name} 无法删除（{exc}）"


def wipe_project_memory(
    project_root: Path,
    *,
    yes: bool,
    stop_holders: bool = True,
) -> dict[str, Any]:
    """删除库与指纹。yes=False 时不改文件。"""
    root = Path(project_root)
    rsi_dir = project_rsi_dir(root)
    if not yes:
        return {
            "ok": False,
            "removed": [],
            "kept": [n for n in KEEP_NAMES if (rsi_dir / n).is_file()],
            "stopped": [],
            "errors": [_YES_HINT],
            "message": _YES_HINT,
        }

    stopped: list[int] = []
    if stop_holders:
        stopped = stop_db_holders(root)
        if stopped:
            time.sleep(0.4)

    removed: list[str] = []
    errors: list[str] = []
    targets = [rsi_dir / name for name in WIPE_NAMES]
    for attempt in range(5):
        errors = []
        pending = [p for p in targets if p.exists()]
        if not pending:
            break
        for path in pending:
            err = _unlink(path)
            if err:
                errors.append(err)
            elif not path.exists():
                if path.name not in removed:
                    removed.append(path.name)
        if not errors:
            break
        if attempt < 4:
            time.sleep(0.3)

    leftover = [p.name for p in targets if p.exists()]
    locked = leftover and errors
    if locked:
        errors.append(
            "库文件仍被占用。请在 Cursor Settings → MCP 里暂时关掉 rsi-boot，再执行 rsi wipe --yes。"
        )
    message = (
        "已清除本项目记忆库（保留 identity.json）。"
        if removed and not leftover
        else (
            "没有需要删除的库文件。"
            if not leftover and not removed
            else "清库未完成。"
        )
    )
    return {
        "ok": not leftover,
        "removed": removed,
        "kept": [n for n in KEEP_NAMES if (rsi_dir / n).is_file()],
        "stopped": stopped,
        "errors": errors,
        "message": message,
    }


def run_wipe(args: Any) -> int:
    explicit = Path(args.project_root) if getattr(args, "project_root", None) else None
    root = resolve_project_root(explicit=explicit)
    result = wipe_project_memory(root, yes=bool(getattr(args, "yes", False)))
    if not result["ok"]:
        for err in result["errors"]:
            print(err, file=sys.stderr)
        return 1 if result["removed"] or result["stopped"] else 2
    if result["stopped"]:
        print("已停止占用进程: " + ", ".join(str(p) for p in result["stopped"]))
    if result["removed"]:
        print("已删除: " + ", ".join(result["removed"]))
    else:
        print(result["message"])
    if result["kept"]:
        print("已保留: " + ", ".join(result["kept"]))
    return 0
