"""学习报告（§10.9.12）：终端摘要 + JSON 落盘（.rsi/bootstrap_report.json）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

_SLICE_LABELS = {
    "source_files": "源文件",
    "chunks": "切出",
    "merged_tiny": "合并碎块",
    "split_large": "拆超长",
    "index_written": "目录条目",
    "skipped_tiny": "过碎跳过",
}
_APPLIED_LABELS = {
    "docs": "文档",
    "code": "代码",
    "config": "配置",
    "git": "Git",
    "correlation": "关联",
}


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
    judge_candidates: int = 0                                   # 候选组数（gate.conflicts 总数）
    judge_unresolved: int = 0                                   # host：队列 items 数；local：0
    judge_queue_path: str = ""                                  # host：.rsi/host_judge_queue.json
    judge_omitted: int = 0                                      # 超 10000 被截断的候选数
    dry_run: bool = False
    will_apply: List[str] = field(default_factory=list)
    will_confirm: List[str] = field(default_factory=list)
    blocked_untagged_archive: int = 0
    wipe_hint: str = ""

    def _format_judge_line(self) -> str:
        if not self.judge:
            return ""
        if self.judge == "host":
            line = (
                f"判断: judge=host，候选 {self.judge_candidates} 组，"
                f"未决 {self.judge_unresolved} 组，工作包 {self.judge_queue_path}"
            )
        else:
            line = f"判断: judge=local，候选 {self.judge_candidates} 组"
        if self.judge_omitted > 0:
            line += (
                f"（超上限截断 {self.judge_omitted} 组未入包，"
                "建议收窄 --include 或分目录再学）"
            )
        return line

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
        return "\n".join(lines)

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
            "## 画像",
        ]
        if self.profile_summary:
            for key, value in self.profile_summary.items():
                if value:
                    lines.append(f"- **{key}**: {value}")
        if not any(self.profile_summary.values()):
            lines.append("（信号不足，留空）")

        lines.extend(["", "## 直通生效"])
        if self.applied:
            for kind in ("docs", "code", "config", "git", "correlation"):
                count = self.applied.get(kind, 0)
                if not count:
                    continue
                label = _APPLIED_LABELS.get(kind, kind)
                lines.append(f"- **{label}**: {count} 条")
                for title in self.applied_samples.get(kind, [])[:15]:
                    lines.append(f"  - {title}")
        else:
            lines.append("（本轮无直通条目）")

        lines.extend(["", "## 切片统计"])
        if self.slice_stats:
            for key, label in _SLICE_LABELS.items():
                if key in self.slice_stats:
                    lines.append(f"- **{label}**: {self.slice_stats[key]}")
        else:
            lines.append("（未扫描文档或未启用 docs 维度）")

        lines.extend(["", "## 抽取待确认"])
        if self.extracts:
            for item in self.extracts:
                lines.append(f"- {item.get('title', '')}（{item.get('source', '')}）")
        else:
            lines.append("（本轮无对话/规则抽取）")

        lines.extend(["", "## 冲突组"])
        if self.conflict_counts:
            lines.append(
                "计数: "
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
        else:
            lines.append("（未检测到冲突组）")
        judge_line = self._format_judge_line()
        if judge_line:
            lines.append(judge_line)

        if self.wipe_hint:
            lines.extend(["", "## 清库重学", self.wipe_hint])
        lines.extend([
            "",
            "## 下一步",
            "打开 Cursor 继续开发时，**下一次任务会在对话里弹出抉择**，"
            "用于确认本轮抽取项与冲突组；无 IDE 时可用 "
            "`rsi knowledge accept` 作为兜底。",
        ])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
