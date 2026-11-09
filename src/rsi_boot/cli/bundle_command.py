"""rsi bundle：.rsi/memory 全量的文件级导出/导入。

git 是记忆迁移的首选通道（memory 默认随库走）；bundle 是无 git
或跨机器拷包时的兜底：导出整棵 memory 目录树 + manifest.json。
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BUNDLE_FORMAT = "rsi-memory-bundle/v1"


def _count_files(path: Path) -> int:
    return sum(1 for p in path.rglob("*") if p.is_file()) if path.is_dir() else 0


def _project_id(project_root: Path) -> str:
    try:
        data = json.loads(
            (project_root / ".rsi" / "identity.json").read_text(encoding="utf-8")
        )
        return str(data.get("project_id") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def export_bundle(project_root: Path, out_dir: Path) -> int:
    src = project_root / ".rsi" / "memory"
    if not src.is_dir():
        print(".rsi/memory 不存在，先跑 rsi bootstrap", file=sys.stderr)
        return 1
    dest = out_dir / "memory"
    if dest.exists():
        print(f"目标已有 memory/：{dest}（请先移走或换个 --out）", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    manifest = {
        "format": BUNDLE_FORMAT,
        "project_id": _project_id(project_root),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "file_count": _count_files(dest),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已导出 {manifest['file_count']} 个记忆文件 → {out_dir}")
    return 0


def import_bundle(project_root: Path, source: Path, replace: bool = False) -> int:
    memory_src = source / "memory"
    if not memory_src.is_dir():
        print(f"bundle 里没有 memory/：{source}", file=sys.stderr)
        return 2
    manifest_path = source / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("format") != BUNDLE_FORMAT:
                print(f"警告: bundle 格式未知（{manifest.get('format')}），按目录导入",
                      file=sys.stderr)
        except json.JSONDecodeError:
            print("警告: manifest.json 损坏，按目录导入", file=sys.stderr)
    dest = project_root / ".rsi" / "memory"
    if replace and dest.is_dir():
        backup = dest.with_name(f"memory.bak-{datetime.now():%Y%m%dT%H%M%S}")
        dest.rename(backup)
        print(f"现有 memory 已备份到 {backup.name}")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(memory_src, dest, dirs_exist_ok=True)
    print(f"已导入 {_count_files(memory_src)} 个记忆文件 → {dest}")
    return 0


def run_bundle(args: Any) -> int:
    project_root = Path(getattr(args, "project_root", None) or ".").resolve()
    try:
        if args.bundle_action == "export":
            return export_bundle(project_root, Path(args.out).resolve())
        return import_bundle(
            project_root, Path(args.source).resolve(),
            replace=bool(getattr(args, "replace", False)),
        )
    except OSError as exc:
        print(f"bundle 失败: {exc}", file=sys.stderr)
        return 1
