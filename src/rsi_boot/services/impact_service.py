"""价值/影响力面板（rsi_impact）：把「RSI 帮了多少」组装成可宣讲、且经得起追问的报告。

诚实原则（务必随结果输出 caveats）：
- 成本侧精确可测：注入 footprint（字符 + token 估算）。
- 收益侧分两类：
  1) 严格可观测：召回量/命中率、召回压缩比（按需只暴露全库的一小部分）、采纳率、
     禁止项合规率（违规避免）、提案通过率——都是本地文件里真数出来的；
  2) 省 token 无法由 RSI 自证，只能「用前 vs 用后」对照，故提供 A/B 计算器：
     用户把宿主自己报的两段用量喂进来，本服务只做差、算比例，绝不凭空造数。

数据来源复用 StatsService（events.jsonl + 记忆库）与 metrics.footprint（磁盘注入产物）
与 metrics.snapshot（机会式日快照曲线）。全部为同步读，工具层用 to_thread 卸载。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..memory.store import MemoryStore
from ..metrics.footprint import estimate_tokens, measure_footprint
from ..metrics.snapshot import append_snapshot, read_snapshots
from .stats_service import StatsService

#: 无对照输入时给出的「如何诚实测省 token」规程（宣讲时可直接念）
_CAPTURE_PROTOCOL = {
    "how": [
        "1) 关 RSI（或新开一个未 serve 的等价项目）连续做 N 个真实任务，记录宿主报告的总 token 用量 T_before；",
        "2) 开 RSI 用同样的任务集再做 N 个，记录 T_after；",
        "3) 每任务省 =（T_before − T_after）/ N，省比 = (T_before − T_after) / T_before；",
        "4) 把这些数字用 ab 参数喂给 rsi_impact，由它做差与归一，来源标注为宿主用量。",
    ],
    "note": "RSI 是零 Key 本地服务，看不到宿主模型真实 token 用量；任何『省了多少 token』必须来自上面的用前/用后对照，而非本工具估算。",
}


class ImpactService:
    def __init__(self, store: MemoryStore, project_root: Optional[Path] = None):
        self._store = store
        self._root = Path(project_root) if project_root else None
        self._stats = StatsService(store)

    # ---- 成本侧：注入 footprint ----
    def footprint(self) -> Dict[str, Any]:
        if self._root is None:
            return {"available": False, "reason": "no project_root"}
        fp = measure_footprint(self._root)
        return {"available": True, **fp.asdict()}

    # ---- 收益侧：召回压缩比（严格可观测，按需暴露 vs 全库）----
    def compaction(self, period: str, project_id: Optional[str]) -> Dict[str, Any]:
        docs = [d for d in self._store.list_official(skip_harvest=True) if d.status == "active"]
        corpus_chars = sum(len(d.title or "") + len(d.content or "") for d in docs)
        by_id = {d.id: d for d in docs}
        events = self._stats.recall_events(period, project_id)
        recalls = len(events)
        hit = 0
        surfaced_ids: set[str] = set()
        for ev in events:
            retrieved = ev.get("retrieved") or []
            if retrieved:
                hit += 1
            for rid in retrieved:
                if isinstance(rid, str):
                    surfaced_ids.add(rid)
        surfaced_chars = sum(
            len(by_id[i].title or "") + len(by_id[i].content or "")
            for i in surfaced_ids if i in by_id
        )
        ratio = round(surfaced_chars / corpus_chars, 4) if corpus_chars else None
        return {
            "recalls": recalls,
            "hit_rate": round(hit / recalls, 4) if recalls else None,
            "unique_surfaced_docs": len(surfaced_ids),
            "surfaced_chars": surfaced_chars,
            "corpus_chars": corpus_chars,
            "exposure_ratio": ratio,
            "kept_out_of_context_chars": max(corpus_chars - surfaced_chars, 0),
            "surfaced_est_tokens": estimate_tokens("x" * surfaced_chars) if surfaced_chars else 0,
            "corpus_est_tokens": estimate_tokens("x" * corpus_chars) if corpus_chars else 0,
        }

    # ---- A/B 省 token 计算器（数字来源=宿主用量，本服务只做差）----
    def ab_savings(self, ab: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(ab, dict):
            return {"measured": False, **_CAPTURE_PROTOCOL}
        before = ab.get("baseline") or {}
        after = ab.get("treatment") or {}

        def _num(v: Any) -> Optional[float]:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        tb, ta = _num(before.get("tokens")), _num(after.get("tokens"))
        nb, na = _num(before.get("tasks")), _num(after.get("tasks"))
        if tb is None or ta is None or not nb or not na:
            return {"measured": False, "reason": "需 baseline/treatment 各含 tokens 与 tasks",
                    **_CAPTURE_PROTOCOL}
        per_before = tb / nb
        per_after = ta / na
        saved_per_task = per_before - per_after
        saved_pct = round(saved_per_task / per_before, 4) if per_before else None
        fp = self.footprint()
        return {
            "measured": True,
            "source": "user-supplied host token usage (before/after)",
            "per_task_before": round(per_before, 2),
            "per_task_after": round(per_after, 2),
            "saved_tokens_per_task": round(saved_per_task, 2),
            "saved_pct": saved_pct,
            "rsi_injected_cost_per_turn_est_tokens": fp.get("per_turn_est_tokens"),
            "note": "净收益 = 上述省量（宿主实测）；RSI 每轮被动注入的估算成本单列，供抵扣参考。",
        }

    # ---- 曲线：优先日快照（长程），回落事件趋势（短程）----
    def curve(self, period: str, project_id: Optional[str]) -> Dict[str, Any]:
        snaps = read_snapshots(self._store.rsi_dir)
        if snaps:
            return {"source": "snapshots", "points": snaps[-90:]}
        trend = self._stats._trend_store(period, project_id)
        return {"source": "events", "points": trend}

    def snapshot_now(self, project_id: Optional[str]) -> Dict[str, Any]:
        """把当前窗口聚合成一条日快照（机会式钩子与 rsi impact 均可调）。同日只落第一条。"""
        comp = self.compaction("week", project_id)
        summary = self._stats._summary_store("week", project_id)
        fp = self.footprint()
        record = {
            "recalls": comp["recalls"],
            "hit_rate": comp["hit_rate"],
            "surfaced_chars": comp["surfaced_chars"],
            "corpus_chars": comp["corpus_chars"],
            "adoption_rate": summary.get("adoption_rate"),
            "compliance": summary.get("prohibition_compliance"),
            "footprint_chars": fp.get("per_turn_chars"),
        }
        appended = append_snapshot(self._store.rsi_dir, record)
        return {"appended": appended, "record": record}

    def report(
        self,
        period: str = "week",
        project_id: Optional[str] = None,
        ab: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        summary = self._stats._summary_store(period, project_id)
        payload = {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "period": period,
            "cost": self.footprint(),
            "recall_value": self.compaction(period, project_id),
            "behavior": {
                "adoption_rate": summary.get("adoption_rate"),
                "prohibition_compliance": summary.get("prohibition_compliance"),
                "prohibition_surfaced": summary.get("prohibition_surfaced"),
                "proposal_pass_rate": summary.get("proposal_pass_rate"),
                "rule_artifacts_active": summary.get("rule_artifacts_active"),
                "avg_recall_latency_ms": summary.get("avg_recall_latency_ms"),
                "memory_inventory": summary.get("memory_inventory"),
            },
            "token_savings_ab": self.ab_savings(ab),
            "curve": self.curve(period, project_id),
        }
        payload["headline"] = self._headline(payload)
        payload["caveats"] = self._caveats(payload)
        return payload

    def export_markdown(self, payload: Dict[str, Any]) -> Path:
        """导出一页纸 markdown 宣讲报告到 ~/.rsi/exports/，返回路径。"""
        import os

        exports = Path(os.environ.get("RSI_HOME", str(Path.home() / ".rsi"))) / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = exports / f"rsi_impact_{payload.get('period', 'week')}_{stamp}.md"
        path.write_text(self._render_md(payload), encoding="utf-8")
        return path

    @staticmethod
    def _render_md(payload: Dict[str, Any]) -> str:
        cost = payload["cost"]
        rv = payload["recall_value"]
        beh = payload["behavior"]
        ab = payload["token_savings_ab"]
        lines = [
            "# RSI 价值报告",
            "",
            f"> 生成 {payload['generated_at']} · 窗口 {payload['period']}",
            "",
            "## 一句话",
            "",
            payload["headline"],
            "",
            "## 成本（被动注入，精确可测）",
            "",
            f"- 每轮注入：约 {cost.get('per_turn_chars', 0)} 字符 ≈ "
            f"{cost.get('per_turn_est_tokens', 0)} token（估算）",
        ]
        for a in (cost.get("artifacts") or []):
            lines.append(f"  - {a['name']}：{a['chars']} 字符 ≈ {a['est_tokens']} token")
        lines += [
            "",
            "## 召回价值（严格可观测）",
            "",
            f"- 召回 {rv['recalls']} 次 · 命中率 {int((rv.get('hit_rate') or 0) * 100)}%",
            f"- 按需只暴露全库 {rv['surfaced_chars']}/{rv['corpus_chars']} 字符"
            f"（{int((rv.get('exposure_ratio') or 0) * 100)}%），"
            f"其余 {rv['kept_out_of_context_chars']} 字符留在库外按需才取",
            "",
            "## 行为收益（学习闭环成效）",
            "",
            f"- 采纳率：{beh['adoption_rate'] if beh['adoption_rate'] is not None else '—'}",
            f"- 禁止项合规：{beh['prohibition_compliance'] if beh['prohibition_compliance'] is not None else '—'}"
            f"（触达 {beh['prohibition_surfaced']} 次）",
            f"- 提案通过率：{beh['proposal_pass_rate'] if beh['proposal_pass_rate'] is not None else '—'}",
            f"- 活跃规则产物：{beh['rule_artifacts_active']} · 平均召回延迟：{beh['avg_recall_latency_ms']} ms",
            "",
            "## 省 token（用前/用后对照）",
            "",
        ]
        if ab.get("measured"):
            lines += [
                f"- 每任务用前 {ab['per_task_before']} → 用后 {ab['per_task_after']} token（宿主实测）",
                f"- 每任务省 {ab['saved_tokens_per_task']} token（省比 {int((ab.get('saved_pct') or 0) * 100)}%）",
            ]
        else:
            lines.append("- 未采集 A/B 用量，故不输出省 token 数字（避免编造）。采集方法：")
            for step in ab.get("how", []):
                lines.append(f"  - {step}")
        lines += ["", "## 曲线", "", f"数据源：{payload['curve']['source']}，共 {len(payload['curve']['points'])} 个点。", "", "## 口径与免责声明", ""]
        for c in payload["caveats"]:
            lines.append(f"- {c}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _headline(payload: Dict[str, Any]) -> str:
        beh = payload["behavior"]
        rv = payload["recall_value"]
        parts = [f"记忆 {sum((beh.get('memory_inventory') or {}).values())} 条"]
        if rv.get("recalls"):
            parts.append(f"周召回 {rv['recalls']} 次·命中 {int((rv.get('hit_rate') or 0) * 100)}%")
        if beh.get("adoption_rate") is not None:
            parts.append(f"采纳率 {int(beh['adoption_rate'] * 100)}%")
        if beh.get("prohibition_compliance") is not None:
            parts.append(f"禁止项合规 {int(beh['prohibition_compliance'] * 100)}%")
        if rv.get("exposure_ratio") is not None:
            parts.append(f"按需只暴露全库 {int(rv['exposure_ratio'] * 100)}% 的内容")
        ab = payload["token_savings_ab"]
        if ab.get("measured"):
            parts.append(f"用前/用后每任务省 {ab['saved_tokens_per_task']} token（{int((ab.get('saved_pct') or 0) * 100)}%，宿主实测）")
        return "；".join(parts) + "。"

    @staticmethod
    def _caveats(payload: Dict[str, Any]) -> List[str]:
        notes = [
            "token 数为启发式估算（CJK≈1/字，其余≈4 字符/token），与宿主真实分词器无关，仅供量级参考。",
            "注入 footprint 是 RSI 每轮『加进』上下文的成本，不是省量；省量只能用 A/B 对照（宿主用量）得出。",
            "召回压缩比按『按需暴露 vs 全库』口径，未假设宿主不用 RSI 就会全量读库，属保守表述。",
            "召回压缩比按 retrieved 命中的现存正式条目计字符，已删除/归档的命中不计入体量（只减不增，属保守）。",
        ]
        if not payload["token_savings_ab"].get("measured"):
            notes.append("未提供 A/B 用量输入，故本次不输出任何『省 token』数字——避免编造；按 capture_protocol 采集后再算。")
        return notes
