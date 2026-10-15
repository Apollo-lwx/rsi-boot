"""rsi_impact 价值面板：注入成本、召回压缩比、A/B 省 token、机会式快照、工具与 CLI 冒烟。"""

from datetime import datetime, timezone

import pytest

from rsi_boot.memory.logstore import append_event
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.services.impact_service import ImpactService


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_doc(store: MemoryStore, *, idx: int, title: str, content: str) -> str:
    doc_id = f"{idx:08x}{'a' * 24}"
    doc = MemoryDoc(id=doc_id, type="convention", title=title, content=content, status="active")
    dest = official_dir(store.rsi_dir, "convention") / memory_filename(title, doc_id)
    store.write(doc, dest=dest)
    return doc_id


# ---------- footprint ----------

def test_estimate_tokens_splits_cjk_and_ascii():
    from rsi_boot.metrics.footprint import estimate_tokens

    assert estimate_tokens("") == 0
    assert estimate_tokens("记忆") == 2            # 每 CJK 字 ≈ 1
    assert estimate_tokens("abcd") == 1            # 4 ascii ≈ 1
    assert estimate_tokens("abcde") == 2           # 5 ascii 向上取整


def test_measure_footprint_reads_rules_and_agents_block(tmp_path):
    from rsi_boot.metrics.footprint import measure_footprint
    from rsi_boot.injector.targets import AgentsMdTarget

    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "rsi-prohibition-abcd1234.mdc").write_text("---\nalwaysApply: true\n---\n禁止 SELECT *", encoding="utf-8")
    (rules / "user-handwritten.mdc").write_text("不该计入" * 100, encoding="utf-8")  # 非 rsi- 前缀，忽略
    (tmp_path / "AGENTS.md").write_text(
        "用户自留地。\n\n" + AgentsMdTarget.BEGIN + "\n托管块内容 abc\n" + AgentsMdTarget.END + "\n尾部",
        encoding="utf-8",
    )
    fp = measure_footprint(tmp_path)
    names = {a.name for a in fp.artifacts}
    assert any("rsi-prohibition-abcd1234.mdc" in n for n in names)
    assert not any("user-handwritten" in n for n in names)
    assert any("AGENTS.md" in n for n in names)
    # 托管块外内容不计入：footprint 远小于整个 AGENTS.md
    assert fp.total_chars < len((tmp_path / "AGENTS.md").read_text(encoding="utf-8")) + 200


def test_measure_footprint_empty_when_no_artifacts(tmp_path):
    from rsi_boot.metrics.footprint import measure_footprint

    fp = measure_footprint(tmp_path)
    assert fp.total_chars == 0 and fp.total_est_tokens == 0


# ---------- compaction ----------

def test_compaction_exposure_ratio(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    a = _write_doc(store, idx=1, title="甲", content="一二三")      # 6 chars
    b = _write_doc(store, idx=2, title="乙", content="四五六")      # 6 chars
    append_event(store.rsi_dir, {"kind": "recall", "ts": _now(), "retrieved": [a], "project_id": None})
    append_event(store.rsi_dir, {"kind": "recall", "ts": _now(), "retrieved": [a, b], "project_id": None})
    svc = ImpactService(store, project_root=tmp_path)
    comp = svc.compaction("week", None)
    assert comp["recalls"] == 2
    assert comp["hit_rate"] == 1.0
    assert comp["unique_surfaced_docs"] == 2
    assert comp["corpus_chars"] == 8
    assert comp["surfaced_chars"] == 8
    assert comp["exposure_ratio"] == 1.0
    assert comp["kept_out_of_context_chars"] == 0


def test_compaction_partial_exposure(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    a = _write_doc(store, idx=1, title="甲", content="一二三")
    _write_doc(store, idx=2, title="乙", content="四五六")
    append_event(store.rsi_dir, {"kind": "recall", "ts": _now(), "retrieved": [a]})
    comp = ImpactService(store, project_root=tmp_path).compaction("week", None)
    assert comp["exposure_ratio"] == 0.5
    assert comp["kept_out_of_context_chars"] == 4


# ---------- A/B ----------

def test_ab_savings_requires_both_periods(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = ImpactService(store, project_root=tmp_path)
    empty = svc.ab_savings(None)
    assert empty["measured"] is False
    assert empty.get("how")                       # 给出采集规程
    assert "note" in empty
    partial = svc.ab_savings({"baseline": {"tokens": 100, "tasks": 5}})
    assert partial["measured"] is False


def test_ab_savings_computes_delta(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = ImpactService(store, project_root=tmp_path)
    out = svc.ab_savings({
        "baseline": {"tokens": 200000, "tasks": 20},   # 10000/任务
        "treatment": {"tokens": 150000, "tasks": 20},  # 7500/任务
    })
    assert out["measured"] is True
    assert out["per_task_before"] == 10000.0
    assert out["per_task_after"] == 7500.0
    assert out["saved_tokens_per_task"] == 2500.0
    assert out["saved_pct"] == 0.25
    assert "host" in out["source"]


# ---------- snapshot ----------

def test_snapshot_appends_once_per_day(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    _write_doc(store, idx=1, title="甲", content="一二三")
    append_event(store.rsi_dir, {"kind": "recall", "ts": _now(), "retrieved": []})
    svc = ImpactService(store, project_root=tmp_path)
    first = svc.snapshot_now(None)
    assert first["appended"] is True
    second = svc.snapshot_now(None)
    assert second["appended"] is False            # 同日节流
    from rsi_boot.metrics.snapshot import read_snapshots
    series = read_snapshots(store.rsi_dir)
    assert len(series) == 1


def test_curve_prefers_snapshots(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    svc = ImpactService(store, project_root=tmp_path)
    svc.snapshot_now(None)
    curve = svc.curve("week", None)
    assert curve["source"] == "snapshots"
    assert len(curve["points"]) >= 1


# ---------- report ----------

def test_report_has_all_sections_and_no_invented_savings(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    a = _write_doc(store, idx=1, title="甲", content="一二三")
    append_event(store.rsi_dir, {"kind": "recall", "ts": _now(), "retrieved": [a]})
    svc = ImpactService(store, project_root=tmp_path)
    payload = svc.report("week", None, None)
    assert set(payload) >= {"cost", "recall_value", "behavior", "token_savings_ab", "curve", "headline", "caveats"}
    assert payload["token_savings_ab"]["measured"] is False
    assert any("编造" in c or "invented" in c for c in payload["caveats"])
    assert payload["headline"]


# ---------- tool ----------

@pytest.mark.asyncio
async def test_impact_tool_returns_panel(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools.impact_tool import handle
    from rsi_boot.bootstrap import build_runtime

    rt = await build_runtime(project_root=tmp_path)
    try:
        a = _write_doc(rt.store, idx=1, title="甲", content="一二三")
        append_event(rt.store.rsi_dir, {"kind": "recall", "ts": _now(), "retrieved": [a]})
        out = await handle(rt, {"period": "week"})
        assert out["status"] == "success"
        assert out["data"]["recall_value"]["recalls"] >= 1
        out2 = await handle(rt, {"ab": {"baseline": {"tokens": 100, "tasks": 1},
                                        "treatment": {"tokens": 80, "tasks": 1}}, "snapshot": True})
        assert out2["data"]["token_savings_ab"]["saved_tokens_per_task"] == 20.0
    finally:
        await rt.close()


# ---------- CLI ----------

def test_cli_impact_json_and_export(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.cli.impact_command import run_impact

    store = MemoryStore(tmp_path / ".rsi")
    a = _write_doc(store, idx=1, title="甲", content="一二三")
    append_event(store.rsi_dir, {"kind": "recall", "ts": _now(), "retrieved": [a]})
    monkeypatch.setattr("rsi_boot.cli.impact_command.resolve_project_root",
                        lambda explicit=None: tmp_path)
    assert run_impact(["--period", "week"]) == 0
    import json
    printed = json.loads(capsys.readouterr().out)
    assert printed["recall_value"]["recalls"] == 1
    assert run_impact(["--md"]) == 0
    assert "召回" in capsys.readouterr().out
