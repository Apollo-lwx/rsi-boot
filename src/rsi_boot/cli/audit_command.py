"""rsi audit：决策日志导出与生命周期清理（借鉴 OpenMausBot decision log）。

- export：审计报告 + 审批事件导出 CSV（公式注入中和 + 脱敏）；
- prune：超保留窗删除、超滚动阈值按月归档（--dry-run 只预览不落盘）。
"""

from __future__ import annotations

import sys
from pathlib import Path


def run_audit(args) -> int:
    from ..learning.audit_store import export_audit_csv, prune_audit_files

    root = Path(getattr(args, "project_root", ".") or ".").resolve()
    rsi_dir = root / ".rsi"
    if not rsi_dir.is_dir():
        print(f"未找到 {rsi_dir}——先在项目根运行 rsi bootstrap 或确认 --project-root", file=sys.stderr)
        return 2

    if args.audit_action == "export":
        dest = Path(args.out).resolve()
        result = export_audit_csv(rsi_dir, dest)
        print(f"已导出 {result['rows']} 行决策日志到 {result['path']}")
        return 0

    if args.audit_action == "prune":
        retention = args.days if args.days and args.days > 0 else None
        result = prune_audit_files(
            rsi_dir, retention_days=retention, dry_run=getattr(args, "dry_run", False),
        )
        prefix = "[dry-run] " if getattr(args, "dry_run", False) else ""
        print(f"{prefix}决策日志清理：按月归档 {result['archived']} 个，超窗删除 {result['pruned']} 个")
        return 0

    print("用法：rsi audit export --out <csv> | rsi audit prune [--days N] [--dry-run]", file=sys.stderr)
    return 2
