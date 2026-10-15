"""rsi impact CLI：在真实项目里跑一段时间后一条命令出价值面板，可导出宣讲用一页纸。

示例：
  rsi impact --period week                     # JSON 面板
  rsi impact --md                              # 人类可读摘要
  rsi impact --export                           # 导出 markdown 到 ~/.rsi/exports/
  rsi impact --before-tokens 120000 --before-tasks 20 \
             --after-tokens  90000 --after-tasks  20   # 用宿主实测用量算省 token
数字口径见 rsi_impact 工具说明：省 token 只由 A/B 对照得出，RSI 不估算宿主用量。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rsi_boot.memory.store import MemoryStore
from rsi_boot.project import resolve_project_root
from rsi_boot.services.impact_service import ImpactService
from rsi_boot.ux.lang import locale_lang
from rsi_boot.ux.messages import t


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rsi impact")
    parser.add_argument("--period", choices=["day", "week", "month"], default="week")
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--lang", choices=["zh", "en"], default=None)
    parser.add_argument("--md", action="store_true", help="打印人类可读摘要（默认 JSON）")
    parser.add_argument("--export", action="store_true", help="导出一页纸 markdown 到 ~/.rsi/exports/")
    parser.add_argument("--snapshot", action="store_true", help="顺带落一条当日快照攒曲线")
    parser.add_argument("--before-tokens", type=float, default=None, help="用前（关 RSI）宿主 token 总量")
    parser.add_argument("--before-tasks", type=float, default=None, help="用前任务数")
    parser.add_argument("--after-tokens", type=float, default=None, help="用后（开 RSI）宿主 token 总量")
    parser.add_argument("--after-tasks", type=float, default=None, help="用后任务数")
    return parser


def _build_ab(args: argparse.Namespace):
    if args.before_tokens is None and args.after_tokens is None:
        return None
    return {
        "baseline": {"tokens": args.before_tokens, "tasks": args.before_tasks},
        "treatment": {"tokens": args.after_tokens, "tasks": args.after_tasks},
    }


def _print_md(payload: dict, lang: str) -> None:
    print(payload["headline"])
    print()
    cost = payload["cost"]
    rv = payload["recall_value"]
    print(t("IMPACT_LINE_COST", lang,
            chars=cost.get("per_turn_chars", 0), tokens=cost.get("per_turn_est_tokens", 0)))
    print(t("IMPACT_LINE_RECALL", lang,
            recalls=rv["recalls"], hit=int((rv.get("hit_rate") or 0) * 100),
            exposure=int((rv.get("exposure_ratio") or 0) * 100)))
    ab = payload["token_savings_ab"]
    if ab.get("measured"):
        print(t("IMPACT_LINE_SAVED", lang,
                saved=ab["saved_tokens_per_task"], pct=int((ab.get("saved_pct") or 0) * 100)))
    else:
        print(t("IMPACT_LINE_NO_AB", lang))


def run_impact(argv: list[str] | None = None) -> int:
    tokens = list(argv) if argv is not None else []
    if tokens and tokens[0] == "--":  # 主 CLI 用 REMAINDER 透传时带回的分隔符，吃掉
        tokens = tokens[1:]
    args = _parser().parse_args(tokens)
    lang = args.lang or locale_lang()
    explicit = Path(args.project_root) if args.project_root else None
    root = resolve_project_root(explicit=explicit)
    rsi = root / ".rsi"
    rsi.mkdir(parents=True, exist_ok=True)
    service = ImpactService(MemoryStore(rsi), project_root=root)

    if args.snapshot:
        service.snapshot_now(None)
    payload = service.report(args.period, None, _build_ab(args))

    if args.export:
        path = service.export_markdown(payload)
        print(t("IMPACT_EXPORTED", lang, path=str(path)))
        return 0
    if args.md:
        _print_md(payload, lang)
        return 0
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0
