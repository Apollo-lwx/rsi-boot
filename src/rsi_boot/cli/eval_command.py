"""rsi eval：固定探针集评测与纵向导出（T2.1）。

  rsi eval replay  [--snapshot N] [--probes PATH] [--project-root PATH]
  rsi eval export  [--project-root PATH] [--out PATH]
  rsi eval tool-choice            # 工具选择案例集离线校验（benchmark/tool-choice）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def run_eval(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="rsi eval")
    sub = parser.add_subparsers(dest="action", required=True)
    p_replay = sub.add_parser("replay", help="对当前（或指定快照）记忆状态跑固定探针集")
    p_replay.add_argument("--snapshot", type=int, default=None)
    p_replay.add_argument("--probes", default=None)
    p_replay.add_argument("--project-root", default=None)
    p_replay.add_argument("--no-log", action="store_true", help="不写入 replay.jsonl")
    p_export = sub.add_parser("export", help="导出 replay 纵向记录为 CSV")
    p_export.add_argument("--project-root", default=None)
    p_export.add_argument("--out", default=None)
    p_tc = sub.add_parser("tool-choice", help="工具选择误用案例集校验（改工具描述前后必跑）")
    p_tc.add_argument("--report", action="store_true", help="同时生成 SUMMARY.md 报告")
    args = parser.parse_args(argv)

    if args.action == "tool-choice":
        # 开发仓库布局：src/rsi_boot/cli/eval_command.py → 仓库根为第 4 层 parent；
        # pip 安装不含 benchmark/ 目录，此时明确报错
        bench = Path(__file__).resolve().parents[3] / "benchmark" / "tool-choice"
        if not (bench / "check.py").is_file():
            print("benchmark/tool-choice 不在包内（pip 安装不含 benchmark 目录）", file=sys.stderr)
            return 2
        sys.path.insert(0, str(bench))
        import check

        argv2 = ["--report"] if args.report else []
        return check.main(argv2)

    root = Path(args.project_root).resolve() if args.project_root else Path.cwd()
    from ..learning import probe_eval

    if args.action == "replay":
        probes = Path(args.probes) if args.probes else probe_eval.default_probe_pack()
        log_to = None if args.no_log else root / ".rsi"
        try:
            summary = asyncio.run(probe_eval.run_probes(
                root, probes, snapshot=args.snapshot, log_to=log_to,
            ))
        except (ValueError, FileNotFoundError) as exc:
            print(f"replay 失败: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["pass_rate"] >= 1.0 else 1

    rows = probe_eval.export_longitudinal(root / ".rsi")
    if args.out:
        Path(args.out).write_text(rows, encoding="utf-8")
        print(f"已导出 -> {args.out}")
    else:
        sys.stdout.write(rows)
    return 0
