"""学习报告（§10.9.12）：终端摘要 + JSON 落盘（.rsi/bootstrap_report.json）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from rsi_boot.scanner.reading_packs import load_index, summarize_domains, summarize_lanes

@dataclass
class BootstrapReport:
    project_root: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    signals: Dict[str, int] = field(default_factory=dict)       # kind -> 发现文件数
    planned_scopes: Dict[str, bool] = field(default_factory=dict)
    knowledge_written: int = 0
    chunks_skipped: int = 0
    duplicates_skipped: int = 0
    archived: int = 0                                           # 信号源删除归档数（§10.9.10）
    superseded: int = 0                                         # 内容变更收敛的旧版本条目数（§10.9.10）
    revived: int = 0                                            # revert/切回复活的条目数
    review_queue_archived: int = 0                              # 已弃用：帽不再 archived
    review_queue_warning: str = ""                              # 带 bootstrap_run_id 的 pending 超过 500 时的警告
    prohibition_seeds: int = 0                                  # 用户规则禁止句式种子数（ISSUE-6）
    version_conflicts: int = 0                                  # 多版本文档家族冲突（待用户裁决）
    code_modules: int = 0                                       # AST 骨架模块数（P2.6）
    git_summary: str = ""                                       # Git 分析摘要（P2.6）
    conversation_patterns: int = 0                              # 对话/决策模式数（P2.6）
    correlations: Dict[str, int] = field(default_factory=dict)  # 关联推理成果（P2.6）
    errors: List[str] = field(default_factory=list)
    profile_summary: Dict[str, str] = field(default_factory=dict)
    applied: Dict[str, int] = field(default_factory=dict)
    applied_samples: Dict[str, List[str]] = field(default_factory=dict)
    slice_stats: Dict[str, int] = field(default_factory=dict)
    extracts: List[Dict[str, str]] = field(default_factory=list)
    conflicts: List[Dict[str, str]] = field(default_factory=list)
    conflict_counts: Dict[str, int] = field(default_factory=dict)
    judge: str = ""                                             # "host" | "local" | ""（dry-run）
    pack_count: int = 0
    pack_omitted_sources: List[str] = field(default_factory=list)
    pack_lanes: List[Dict[str, Any]] = field(default_factory=list)
    pack_domains: List[Dict[str, Any]] = field(default_factory=list)
    pack_done_count: int = 0
    pack_skipped_count: int = 0
    pack_pending_count: int = 0
    distilled_count: int = 0
    harvest_warning: str = ""                                   # 赋值在 Task 7
    dry_run: bool = False
    will_apply: List[str] = field(default_factory=list)
    will_confirm: List[str] = field(default_factory=list)
    blocked_untagged_archive: int = 0
    wipe_hint: str = ""

    def _format_judge_line(self) -> str:
        if not self.judge:
            return ""
        if self.judge == "host":
            return f"判断: judge=host，阅读包 {self.pack_count} 个"
        return (
            f"判断: judge=local，阅读包 {self.pack_count} 个；"
            "在当前宿主按 rsi-relearn 继续"
        )

    def render_terminal(self) -> str:
        lines = ["", "=== RSI Boot 学习报告 ===", f"项目: {self.project_root}"]
        lines.append("信号发现: " + (", ".join(f"{k}({v})" for k, v in self.signals.items() if v) or "无"))
        skipped = [k for k, v in self.planned_scopes.items() if not v]
        if skipped:
            lines.append(f"跳过维度: {', '.join(skipped)}")
        if self.dry_run:
            lines.append("将直通: " + (", ".join(self.will_apply) if self.will_apply else "（无）"))
            lines.append("将进确认: " + (", ".join(self.will_confirm) if self.will_confirm else "（无）"))
            lines.append("（dry-run：未写入任何数据）")
        else:
            lines.append(
                f"知识写入: {self.knowledge_written} 条；跳过: {self.chunks_skipped}；"
                f"去重: {self.duplicates_skipped}；归档: {self.archived}"
            )
            if self.superseded or self.revived:
                lines.append(
                    f"变更收敛: {self.superseded} 条旧版本归档；复活: {self.revived} 条（§10.9.10）"
                )
            judge_line = self._format_judge_line()
            if judge_line:
                lines.append(judge_line)
            if self.harvest_warning:
                lines.append(self.harvest_warning)
            if self.conflict_counts:
                total = sum(self.conflict_counts.values())
                detail = " · ".join(
                    f"{name} {n}" for name, n in sorted(self.conflict_counts.items()) if n
                )
                lines.append(f"冲突: {detail}（共 {total} 组，报告只留样例）")
            elif self.version_conflicts:
                lines.append(
                    f"版本冲突: {self.version_conflicts} 组（rsi_conflicts 裁决，不自动归档）"
                )
            if self.prohibition_seeds:
                lines.append(f"禁止项种子: {self.prohibition_seeds} 条（来自用户规则文件，待审批）")
            if self.review_queue_warning:
                lines.append(f"审批队列: {self.review_queue_warning}")
            if self.wipe_hint:
                lines.append(self.wipe_hint)
            if self.review_queue_archived:
                lines.append(
                    f"审批队列限量: {self.review_queue_archived} 条溢出置 archived"
                    "（rsi_knowledge_review 批量审批可恢复）"
                )
            if self.code_modules:
                lines.append(f"代码骨架: {self.code_modules} 个模块")
            if self.git_summary:
                lines.append(f"Git: {self.git_summary}")
            if self.conversation_patterns:
                lines.append(f"对话模式: {self.conversation_patterns} 条")
            if self.correlations:
                lines.append("关联推理: " + ", ".join(f"{k}={v}" for k, v in sorted(self.correlations.items())))
            if self.profile_summary:
                summary = ", ".join(f"{k}={v}" for k, v in self.profile_summary.items() if v)
                lines.append(f"画像: {summary or '信号不足，留空'}")
        if self.errors:
            lines.append(f"错误 {len(self.errors)} 条（详见 JSON 报告）")
        if self.pack_lanes:
            lines.append("板块: " + " · ".join(
                f"{lane.get('label', lane.get('id'))} {lane.get('total', 0)}"
                for lane in self.pack_lanes
            ))
        if not self.dry_run and self.pack_count:
            lines.append(
                f"进度: done {self.pack_done_count} / skipped {self.pack_skipped_count} / "
                f"pending {self.pack_pending_count}；蒸馏条 {self.distilled_count}"
            )
            if self.pack_pending_count:
                lines.append("本轮按板块并行蒸完全部 pending，不要停到下次对话。")
            elif self.pack_skipped_count:
                lines.append(
                    f"未覆盖: skipped {self.pack_skipped_count}，不算学完。"
                )
        return "\n".join(lines)

    def apply_pack_inventory(
        self,
        packs: list[dict[str, Any]],
        *,
        distilled_count: int | None = None,
    ) -> None:
        items = [p for p in packs if isinstance(p, dict) and p.get("id")]
        self.pack_count = len(items)
        self.pack_lanes = summarize_lanes(items)
        self.pack_domains = summarize_domains(items)
        self.pack_done_count = sum(1 for p in items if p.get("status") == "done")
        self.pack_skipped_count = sum(1 for p in items if p.get("status") == "skipped")
        self.pack_pending_count = sum(
            1 for p in items if p.get("status") not in {"done", "skipped"}
        )
        if distilled_count is not None:
            self.distilled_count = distilled_count
            self.knowledge_written = distilled_count

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload["conflicts"] = list(payload.get("conflicts") or [])[:30]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_markdown(self, path: Path) -> None:
        lines: List[str] = [
            "# RSI Boot 学习报告",
            "",
            f"项目: {self.project_root}",
            f"开始: {self.started_at}",
            "",
            "## 仓库信号",
        ]
        if self.signals:
            for key, value in self.signals.items():
                if value:
                    label = "git 提交信号" if key == "git" else key
                    lines.append(f"- {label}: {value}")
        else:
            lines.append("- （采集未记录信号）")
        skipped = [k for k, v in self.planned_scopes.items() if not v]
        if skipped:
            lines.append(f"- 跳过维度: {', '.join(skipped)}")
        if self.git_summary:
            lines.append(f"- git 摘要: {self.git_summary}")
        if self.code_modules:
            lines.append(f"- 代码骨架模块: {self.code_modules}")
        if self.harvest_warning:
            lines.append(f"- {self.harvest_warning}")

        lines.extend(["", "## 阅读包"])
        lines.append(f"- 合计 {self.pack_count} 个")
        if self.pack_lanes:
            lines.append("- 按板块（可并行蒸馏，板块之间默认不打架；冲突留给召回/rsi_conflicts）：")
            for lane in self.pack_lanes:
                lines.append(
                    f"  - **{lane.get('label') or lane.get('id')}**: "
                    f"{lane.get('total', 0)} 包 / {lane.get('sources', 0)} 源"
                    f"（pending {lane.get('pending', 0)}，"
                    f"done {lane.get('done', 0)}，skipped {lane.get('skipped', 0)}）"
                )
        if self.pack_domains:
            lines.append("- 包数最多的域：")
            for item in self.pack_domains:
                lines.append(
                    f"  - `{item.get('domain')}`: "
                    f"{item.get('packs', 0)} 包 / {item.get('sources', 0)} 源"
                )
        omitted = list(self.pack_omitted_sources or [])
        if omitted:
            lines.append(f"- 未入包源 {len(omitted)} 条（最多列出 20）")
            for src in omitted[:20]:
                lines.append(f"  - {src}")

        lines.extend(["", "## 蒸馏进度"])
        lines.append(
            f"- 阅读包: done {self.pack_done_count} / "
            f"skipped {self.pack_skipped_count} / pending {self.pack_pending_count}"
        )
        lines.append(f"- 已写入蒸馏条: {self.distilled_count}")
        if self.pack_pending_count:
            lines.append("- 本轮应把 pending 全部蒸完，不要留到下次对话。")
        elif self.pack_skipped_count:
            uncovered = [
                str(lane.get("label") or lane.get("id"))
                for lane in self.pack_lanes
                if int(lane.get("skipped") or 0) and not int(lane.get("done") or 0)
            ]
            lines.append(
                f"- skipped {self.pack_skipped_count} 包视为未覆盖，不算学完。"
            )
            if uncovered:
                lines.append("- 整板未覆盖: " + "、".join(uncovered))
            if self.pack_done_count:
                lines.append("- 有蒸馏的板块可审批；未覆盖板块必须补蒸，禁止整板 skip。")
        elif self.pack_count:
            if self.distilled_count:
                lines.append("- 阅读包均已蒸馏。条已 active 则召回可用，否则先 rsi_knowledge_review。")
            else:
                lines.append("- 阅读包均已蒸馏。批准本 run 后召回才可用。")
        else:
            lines.append("- 采集阶段不写知识；宿主按板块蒸馏后回写本段。")

        lines.extend(["", "## 画像"])
        judge_line = self._format_judge_line()
        if judge_line:
            lines.append(f"- {judge_line}")
        if self.profile_summary and any(self.profile_summary.values()):
            for key, value in self.profile_summary.items():
                if value:
                    lines.append(f"- **{key}**: {value}")
        else:
            lines.append("- （信号不足，留空）")
        if self.conflict_counts:
            lines.append(
                "- 冲突计数: "
                + " · ".join(
                    f"{name} {n}" for name, n in sorted(self.conflict_counts.items())
                )
            )
        if self.conflicts:
            sample = self.conflicts[:30]
            for item in sample:
                lines.append(
                    f"- **{item.get('type', '')}**: "
                    f"{item.get('left', '')} ↔ {item.get('right', '')} — "
                    f"{item.get('reason', '')}"
                )
            extra = max(0, sum(self.conflict_counts.values()) - len(sample)) if self.conflict_counts else max(0, len(self.conflicts) - 30)
            if extra:
                lines.append(f"（仅列出前 {len(sample)} 条样例，其余 {extra} 组见库内 rsi_conflicts）")

        if self.wipe_hint:
            lines.extend(["", "## 清库重学", self.wipe_hint])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def report_from_json(path: Path) -> BootstrapReport | None:
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw.get("project_root"):
        return None
    allowed = {item.name for item in fields(BootstrapReport)}
    payload = {key: value for key, value in raw.items() if key in allowed}
    return BootstrapReport(**payload)


def count_distilled(store) -> int:
    """数 signal:distilled。只扫文本，不 hydrate 全文，避免每条 pack_done 拖死 MCP。"""
    root = Path(store.rsi_dir) / "memory"
    if not root.is_dir():
        return 0
    n = 0
    for path in root.rglob("*.yaml"):
        if path.name.endswith(".tmp"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "signal:distilled" in text:
            n += 1
    return n


def refresh_report(rsi_dir: Path, store=None) -> None:
    rsi_dir = Path(rsi_dir)
    packs = [
        item
        for item in (load_index(rsi_dir).get("packs") or [])
        if isinstance(item, dict) and item.get("id")
    ]
    report = report_from_json(rsi_dir / "bootstrap_report.json")
    if report is None:
        report = BootstrapReport(project_root=str(rsi_dir.parent))
    distilled = count_distilled(store) if store is not None else report.distilled_count
    report.apply_pack_inventory(packs, distilled_count=distilled)
    report.write_json(rsi_dir / "bootstrap_report.json")
    report.write_markdown(rsi_dir / "bootstrap_report.md")
