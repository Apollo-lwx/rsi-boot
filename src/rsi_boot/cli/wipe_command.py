"""rsi wipe：清掉本项目文件记忆树与指纹，保留 identity.json。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..project import project_rsi_dir, resolve_project_root
from ..ux.lang import locale_lang
from ..ux.messages import t

WIPE_TREES = ("memory", "logs", "cache", "audit", "state")
WIPE_LEFTOVER = ("rsi.db", "rsi.db-wal", "rsi.db-shm", "manifest.json")
KEEP_NAMES = ("identity.json",)

_YES_HINT = lambda lang: t("WIPE_HINT", lang)


def _norm(path: Path | str) -> str:
    return os.path.normcase(str(Path(path).resolve()))


_SERVE_RE = re.compile(
    r"rsi(?:\.exe)?\s+serve|\brsi_boot\b.*\bserve\b",
    re.IGNORECASE,
)
_PROJECT_ROOT_RE = re.compile(
    r"--project-root(?:\s+|=)(?:\"([^\"]+)\"|'([^']+)'|(\S+))",
    re.IGNORECASE,
)


def _cmdline_project_root(cmdline: str) -> str | None:
    match = _PROJECT_ROOT_RE.search(cmdline)
    if match is None:
        return None
    return next((group for group in match.groups() if group), None)


def _is_holder_cmdline(cmdline: str, project_root: Path) -> bool:
    if not cmdline or not _SERVE_RE.search(cmdline):
        return False
    specified = _cmdline_project_root(cmdline)
    if specified:
        try:
            return _norm(specified) == _norm(project_root)
        except OSError:
            return False
    # Cursor MCP: `python -m rsi_boot serve` with RSI_PROJECT_ROOT in env only.
    return True


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


def _rmtree(path: Path) -> str | None:
    try:
        shutil.rmtree(path)
        return None
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"{path.name} 无法删除（{exc}）"


def _remove_path(path: Path) -> str | None:
    if path.is_dir() and not path.is_symlink():
        return _rmtree(path)
    return _unlink(path)


def _wipe_targets(rsi_dir: Path) -> list[Path]:
    return [rsi_dir / name for name in (*WIPE_TREES, *WIPE_LEFTOVER)]


def wipe_project_memory(
    project_root: Path,
    *,
    yes: bool,
    stop_holders: bool = True,
    lang: str | None = None,
) -> dict[str, Any]:
    """删除文件记忆树与残留库文件。yes=False 时不改文件。"""
    loc = lang or locale_lang()
    root = Path(project_root)
    rsi_dir = project_rsi_dir(root)
    hint = _YES_HINT(loc)
    if not yes:
        return {
            "ok": False,
            "removed": [],
            "kept": [n for n in KEEP_NAMES if (rsi_dir / n).is_file()],
            "stopped": [],
            "errors": [hint],
            "message": hint,
        }

    stopped: list[int] = []
    if stop_holders:
        stopped = stop_db_holders(root)
        if stopped:
            time.sleep(0.4)

    removed: list[str] = []
    errors: list[str] = []
    targets = _wipe_targets(rsi_dir)
    for attempt in range(5):
        errors = []
        pending = [p for p in targets if p.exists() and p.name not in KEEP_NAMES]
        if not pending:
            break
        for path in pending:
            err = _remove_path(path)
            if err:
                errors.append(err)
            elif not path.exists():
                if path.name not in removed:
                    removed.append(path.name)
        if not errors:
            break
        if attempt < 4:
            time.sleep(0.3)

    leftover = [p.name for p in targets if p.exists() and p.name not in KEEP_NAMES]
    locked = leftover and errors
    if locked:
        errors.append(
            "记忆文件仍被占用。请在 Cursor Settings → MCP 里暂时关掉 rsi-boot，再执行 rsi wipe --yes。"
        )
    if leftover:
        message = "清库未完成。"
    elif removed:
        message = t("WIPE_DONE", loc)
    else:
        message = t("WIPE_DONE", loc)
    return {
        "ok": not leftover,
        "removed": removed,
        "kept": [n for n in KEEP_NAMES if (rsi_dir / n).is_file()],
        "stopped": stopped,
        "errors": errors,
        "message": message,
    }


def run_wipe(args: Any) -> int:
    loc = getattr(args, "lang", None) or locale_lang()
    explicit = Path(args.project_root) if getattr(args, "project_root", None) else None
    root = resolve_project_root(explicit=explicit)
    result = wipe_project_memory(
        root, yes=bool(getattr(args, "yes", False)), lang=loc,
    )
    if not result["ok"]:
        for err in result["errors"]:
            print(err, file=sys.stderr)
        return 1 if result["removed"] or result["stopped"] else 2
    if result["stopped"]:
        print("已停止占用进程: " + ", ".join(str(p) for p in result["stopped"]))
    if result["removed"]:
        print(t("WIPE_DONE", loc) + " " + ", ".join(result["removed"]))
    else:
        print(result["message"])
    if result["kept"]:
        print(t("WIPE_KEPT", loc, path="identity.json"))
    return 0
