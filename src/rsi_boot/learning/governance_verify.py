"""治理不变量运行时校验（T2.3）：论文声明的两条安全性质可被第三方验证。

- I1 有限漂移：任意 7 天窗内单槽位变更 ≤ SLOT_WEEKLY_CAP（3）次
- I2 不可自指：可写面 W = {prompt_template, skill, strategy, intent_rule, knowledge}；
  门禁参数、评估代码、提案机制自身不在可写面——扫描全部提案记录，
  任何槽位越界或 target_ref 指向门禁/评估构件的提案都是违例证据。

`rsi governance verify` 输出证明性日志；退出码 0 = 不变量成立。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .proposal_engine import SLOT_WEEKLY_CAP, WRITABLE_SLOTS

#: 门禁/评估构件的标识——target_ref 命中任一即视为 I2 违例
_PROTECTED_REFS = (
    "static_gate", "regression_gate", "proposal_engine", "gate",
    "probe_eval", "governance",
)


def _parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _iter_proposals(rsi_dir: Path) -> List[Dict[str, Any]]:
    root = Path(rsi_dir) / "state" / "proposals"
    if not root.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for path in sorted(root.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def verify(rsi_dir: Path, *, now: datetime | None = None) -> Dict[str, Any]:
    """校验 I1 / I2。返回 {passed, invariants: {...}, violations: [...]}."""
    now = now or datetime.now(timezone.utc)
    proposals = _iter_proposals(rsi_dir)
    violations: List[str] = []

    # I1：近 7 天单槽位变更频率
    window_start = now - timedelta(days=7)
    per_slot: Dict[str, int] = {}
    for row in proposals:
        if row.get("status") not in ("active", "rolled_back"):
            continue
        ts = _parse_ts(row.get("activated_at") or row.get("decided_at"))
        if ts is None or ts < window_start:
            continue
        slot = str(row.get("slot") or "")
        per_slot[slot] = per_slot.get(slot, 0) + 1
    for slot, count in per_slot.items():
        if count > SLOT_WEEKLY_CAP:
            violations.append(f"I1 违例：槽位 {slot} 近 7 天变更 {count} 次（上限 {SLOT_WEEKLY_CAP}）")

    # I2：可写面自指检查（常量面 + 提案记录面）
    if "gate" in WRITABLE_SLOTS or "evaluation" in WRITABLE_SLOTS:
        violations.append("I2 违例：WRITABLE_SLOTS 含门禁/评估槽位")
    for row in proposals:
        slot = str(row.get("slot") or "")
        pid = str(row.get("id") or "?")[:8]
        if slot and slot not in WRITABLE_SLOTS:
            violations.append(f"I2 违例：提案 {pid} 槽位 {slot!r} 不在可写面")
        target = str(row.get("target_ref") or "").lower()
        if any(ref in target for ref in _PROTECTED_REFS):
            violations.append(f"I2 违例：提案 {pid} target_ref 指向受保护构件 {target!r}")

    return {
        "passed": not violations,
        "checked_at": now.isoformat(),
        "proposals_scanned": len(proposals),
        "window_days": 7,
        "slot_weekly_cap": SLOT_WEEKLY_CAP,
        "invariants": {
            "I1_bounded_drift": {
                "per_slot_changes_7d": per_slot,
                "ok": not any(v.startswith("I1") for v in violations),
            },
            "I2_no_self_reference": {
                "writable_slots": list(WRITABLE_SLOTS),
                "protected_refs": list(_PROTECTED_REFS),
                "ok": not any(v.startswith("I2") for v in violations),
            },
        },
        "violations": violations,
    }
