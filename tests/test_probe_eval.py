"""T2.1 探针评测：包加载、端到端 replay、纵向导出、CLI。"""

from __future__ import annotations

import json

import pytest

from rsi_boot.cli.eval_command import run_eval
from rsi_boot.learning import probe_eval

PACK = probe_eval.default_probe_pack()


class TestLoadProbes:
    def test_pack_loads(self):
        pack = probe_eval.load_probes(PACK)
        assert pack["version"] == "1.0"
        assert len(pack["seed_docs"]) == 8
        kinds = {p["kind"] for p in pack["probes"]}
        assert kinds == {"recall", "prohibition", "abstain"}

    def test_unknown_kind_rejected(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "version: '0'\nprobes:\n  - {id: x, kind: bogus, question: q}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="未知探针类型"):
            probe_eval.load_probes(bad)

    def test_empty_pack_rejected(self, tmp_path):
        bad = tmp_path / "empty.yaml"
        bad.write_text("version: '0'\nprobes: []\n", encoding="utf-8")
        with pytest.raises(ValueError, match="探针包为空"):
            probe_eval.load_probes(bad)


class TestRunProbes:
    @pytest.mark.asyncio
    async def test_replay_on_empty_project(self, tmp_path):
        summary = await probe_eval.run_probes(tmp_path, PACK)
        assert summary["total"] == 16
        assert summary["passed"] >= 12  # 大多数应命中；个别弱命中允许失败
        assert set(summary["per_kind"]) == {"recall", "prohibition", "abstain"}
        for kind, cell in summary["per_kind"].items():
            assert cell["n"] > 0
            assert 0.0 <= cell["pass_rate"] <= 1.0

    @pytest.mark.asyncio
    async def test_replay_logs_when_log_to(self, tmp_path):
        await probe_eval.run_probes(tmp_path, PACK, log_to=tmp_path / ".rsi")
        rows = probe_eval.read_replay_log(tmp_path / ".rsi")
        assert len(rows) == 1
        assert rows[0]["probe_pack"] == "1.0"

    @pytest.mark.asyncio
    async def test_abstain_probes_pass(self, tmp_path):
        summary = await probe_eval.run_probes(tmp_path, PACK)
        abstain = summary["per_kind"]["abstain"]
        assert abstain["pass_rate"] == 1.0, summary["failures"]


class TestExport:
    def test_export_csv_shape(self, tmp_path):
        rsi = tmp_path / ".rsi"
        probe_eval.append_replay_log(rsi, {
            "ts": "2026-11-03T00:00:00+00:00", "snapshot": 3, "probe_pack": "1.0",
            "total": 16, "passed": 14, "pass_rate": 0.875,
            "per_kind": {"recall": {"n": 12, "pass_rate": 0.917}},
            "failures": ["r03", "a01"],
        })
        csv_text = probe_eval.export_longitudinal(rsi)
        lines = csv_text.strip().splitlines()
        assert lines[0].startswith("ts,snapshot")
        assert "0.875" in lines[1] and "r03|a01" in lines[1]

    def test_export_empty(self, tmp_path):
        assert probe_eval.export_longitudinal(tmp_path / ".rsi").count("\n") == 1


class TestCli:
    def test_replay_cli(self, tmp_path, capsys):
        code = run_eval(["replay", "--project-root", str(tmp_path), "--no-log"])
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["total"] == 16
        assert code in (0, 1)  # 通过率决定退出码，两者都算命令本身成功

    def test_export_cli(self, tmp_path, capsys):
        code = run_eval(["export", "--project-root", str(tmp_path)])
        assert code == 0
        assert "ts,snapshot" in capsys.readouterr().out
