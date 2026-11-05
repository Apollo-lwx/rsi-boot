"""T2.3 治理不变量校验。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import yaml

from rsi_boot.learning.governance_verify import verify


def _proposal(rsi_dir, pid: str, **fields) -> None:
    root = rsi_dir / "state" / "proposals"
    root.mkdir(parents=True, exist_ok=True)
    data = {"id": pid, "status": "active", "slot": "skill",
            "activated_at": datetime.now(timezone.utc).isoformat()}
    data.update(fields)
    (root / f"{pid}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


class TestGovernanceVerify:
    def test_empty_project_passes(self, tmp_path):
        report = verify(tmp_path / ".rsi")
        assert report["passed"]
        assert report["proposals_scanned"] == 0

    def test_i1_normal_churn_passes(self, tmp_path):
        rsi = tmp_path / ".rsi"
        for i in range(3):
            _proposal(rsi, f"p{i}", slot="skill")
        report = verify(rsi)
        assert report["passed"], report["violations"]
        assert report["invariants"]["I1_bounded_drift"]["per_slot_changes_7d"] == {"skill": 3}

    def test_i1_violation_detected(self, tmp_path):
        rsi = tmp_path / ".rsi"
        for i in range(4):
            _proposal(rsi, f"p{i}", slot="knowledge")
        report = verify(rsi)
        assert not report["passed"]
        assert any("I1" in v for v in report["violations"])

    def test_i1_old_changes_out_of_window(self, tmp_path):
        rsi = tmp_path / ".rsi"
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        for i in range(5):
            _proposal(rsi, f"p{i}", slot="skill", activated_at=old)
        report = verify(rsi)
        assert report["passed"]

    def test_i2_out_of_surface_slot_detected(self, tmp_path):
        rsi = tmp_path / ".rsi"
        _proposal(rsi, "p1", slot="gate")
        report = verify(rsi)
        assert not report["passed"]
        assert any("I2" in v and "可写面" in v for v in report["violations"])

    def test_i2_protected_target_detected(self, tmp_path):
        rsi = tmp_path / ".rsi"
        _proposal(rsi, "p1", slot="strategy", target_ref="regression_gate.params")
        report = verify(rsi)
        assert not report["passed"]
        assert any("受保护构件" in v for v in report["violations"])

    def test_pending_proposals_not_counted_for_i1(self, tmp_path):
        rsi = tmp_path / ".rsi"
        for i in range(6):
            _proposal(rsi, f"p{i}", slot="skill", status="proposed")
        report = verify(rsi)
        assert report["passed"]
