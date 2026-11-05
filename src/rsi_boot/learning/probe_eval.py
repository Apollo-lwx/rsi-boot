"""固定探针集评测（T2.1）：治理环的纵向可证性。

与 regression_gate 的"近 30 天抽样回放"互补：探针集固定且随仓库版本化，
才有跨快照版本的可比性——"第 N+1 轮是否比第 N 轮好"由此量化。

探针包（benchmark/probes/probes.yaml）是自洽的：自带 seed_docs，
评测时写入临时工作区，不污染项目真实记忆。

探针类型：
- recall      期望召回命中（标题/正文含 hit_any 之一，top_k 内）
- prohibition 期望 prohibition 通道命中（hit_any 之一）
- abstain     期望不命中（top_k 为空；防误召回）

replay 模式：把项目 .rsi 复制到临时目录，SnapshotStore.switch 到目标版本
（只改临时副本），再叠加探针种子文档后逐题召回。结果追加到
.rsi/state/replay.jsonl，供 `rsi eval export` 画纵向曲线。
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

PROBE_KINDS = ("recall", "prohibition", "abstain")
REPLAY_LOG = "replay.jsonl"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_probes(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"探针包顶层必须是 mapping: {path}")
    probes = data.get("probes") or []
    if not probes:
        raise ValueError(f"探针包为空: {path}")
    for probe in probes:
        if probe.get("kind") not in PROBE_KINDS:
            raise ValueError(f"未知探针类型 {probe.get('kind')!r}: {probe.get('id')}")
    return {
        "version": str(data.get("version") or "0"),
        "seed_docs": data.get("seed_docs") or [],
        "probes": probes,
    }


def default_probe_pack() -> Path:
    return Path(__file__).resolve().parents[3] / "benchmark" / "probes" / "probes.yaml"


def _seed_docs(rsi_dir: Path, seed_docs: List[Dict[str, Any]]) -> None:
    from ..memory.paths import official_dir
    from ..memory.store import MemoryStore, memory_filename
    from ..memory.types import MemoryDoc

    store = MemoryStore(rsi_dir)
    for row in seed_docs:
        doc = MemoryDoc(
            id=str(row["id"]),
            type=str(row.get("type") or "convention"),
            title=str(row["title"])[:120],
            content=str(row.get("content") or "")[:20000],
            tags=list(row.get("tags") or []),
        )
        dest = official_dir(rsi_dir, doc.type) / memory_filename(doc.title, doc.id)
        store.write(doc, dest=dest)


def _hit(payload: Dict[str, Any], needle: str, *, top_k: int) -> bool:
    needle_lower = needle.lower()
    buckets = ("prohibitions", "items", "gene_cases", "teaching_cases", "episodes")
    count = 0
    for bucket in buckets:
        for row in payload.get(bucket) or []:
            count += 1
            if count > top_k:
                return False
            hay = f"{row.get('id', '')} {row.get('title', '')} {row.get('content', '')}".lower()
            if needle_lower in hay:
                return True
    return False


def _any_hit(payload: Dict[str, Any], *, top_k: int) -> bool:
    for bucket in ("prohibitions", "items", "gene_cases", "teaching_cases"):
        if payload.get(bucket):
            return True
    return False


async def run_probes(
    project_root: Path,
    probes_path: Path,
    *,
    snapshot: Optional[int] = None,
    log_to: Optional[Path] = None,
) -> Dict[str, Any]:
    """对（可选快照版本的）记忆状态跑固定探针集。返回聚合结果。"""
    pack = load_probes(probes_path)
    project_root = Path(project_root)
    live_rsi = project_root / ".rsi"

    with tempfile.TemporaryDirectory(prefix="rsi-probe-") as tmp:
        work = Path(tmp)
        if snapshot is not None and live_rsi.is_dir():
            # 仅快照回放需要项目真实记忆；日志不复制（回放语义不含反馈信号，
            # 且大日志会让每次召回的反馈扫描拖慢整个评测）
            shutil.copytree(
                live_rsi, work / ".rsi",
                ignore=shutil.ignore_patterns("logs", "cache", "work", "__pycache__"),
            )
        (work / ".rsi").mkdir(exist_ok=True)

        version_resolved = snapshot
        if snapshot is not None:
            from ..memory.store import MemoryStore
            from .snapshot_store import SnapshotStore

            store = MemoryStore(work / ".rsi")
            snaps = SnapshotStore(store)
            project_id = _project_id_for(work)
            ok = await snaps.switch(project_id, int(snapshot))
            if not ok:
                raise ValueError(f"快照 v{snapshot} 不存在（项目 {project_id}）")

        _seed_docs(work / ".rsi", pack["seed_docs"])

        from ..bootstrap import build_runtime

        runtime = await build_runtime(project_root=work)
        results: List[Dict[str, Any]] = []
        try:
            for probe in pack["probes"]:
                top_k = int(probe.get("top_k") or 5)
                payload = await runtime.recall.recall(
                    str(probe["question"]), _project_id_for(work),
                    user_id="probe", top_k=top_k,
                )
                kind = probe["kind"]
                if kind == "abstain":
                    passed = not _any_hit(payload, top_k=top_k)
                else:
                    needles = probe.get("hit_any") or []
                    passed = any(_hit(payload, str(n), top_k=top_k) for n in needles)
                results.append({
                    "id": probe.get("id"),
                    "kind": kind,
                    "passed": bool(passed),
                })
        finally:
            await runtime.close()

    by_kind: Dict[str, List[bool]] = {}
    for row in results:
        by_kind.setdefault(row["kind"], []).append(row["passed"])
    summary = {
        "ts": _utc_iso(),
        "snapshot": version_resolved,
        "probe_pack": pack["version"],
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "per_kind": {
            kind: {
                "n": len(rows),
                "pass_rate": sum(rows) / len(rows) if rows else 0.0,
            }
            for kind, rows in sorted(by_kind.items())
        },
        "failures": [r["id"] for r in results if not r["passed"]],
    }
    summary["pass_rate"] = summary["passed"] / summary["total"] if summary["total"] else 0.0

    if log_to is not None:
        append_replay_log(log_to, summary)
    return summary


def _project_id_for(project_root: Path) -> str:
    from ..project import derive_project_id

    return derive_project_id(project_root)


def append_replay_log(rsi_dir: Path, summary: Dict[str, Any]) -> None:
    path = Path(rsi_dir) / "state" / REPLAY_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")


def read_replay_log(rsi_dir: Path) -> List[Dict[str, Any]]:
    path = Path(rsi_dir) / "state" / REPLAY_LOG
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def export_longitudinal(rsi_dir: Path) -> str:
    """纵向导出 CSV：每次 replay 一行，含快照版本与分类通过率。"""
    rows = read_replay_log(rsi_dir)
    header = ["ts", "snapshot", "probe_pack", "total", "passed", "pass_rate",
              "recall_pass", "prohibition_pass", "abstain_pass", "failures"]
    lines = [",".join(header)]
    for row in rows:
        per_kind = row.get("per_kind") or {}

        def _rate(kind: str) -> str:
            cell = per_kind.get(kind) or {}
            rate = cell.get("pass_rate")
            return "" if rate is None else f"{rate:.3f}"

        snapshot = row.get("snapshot")
        failures = "|".join(str(f) for f in (row.get("failures") or []))
        lines.append(",".join([
            str(row.get("ts") or ""),
            "" if snapshot is None else str(snapshot),
            str(row.get("probe_pack") or ""),
            str(row.get("total") or 0),
            str(row.get("passed") or 0),
            f"{float(row.get('pass_rate') or 0.0):.3f}",
            _rate("recall"), _rate("prohibition"), _rate("abstain"),
            failures,
        ]))
    return "\n".join(lines) + "\n"
