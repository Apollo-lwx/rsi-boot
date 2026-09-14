"""File-backend review/snapshots/arms: list empty state/ and seed arms.yaml."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from rsi_boot.learning.proposal_engine import ProposalEngine
from rsi_boot.learning.snapshot_store import SnapshotStore
from rsi_boot.memory.logstore import append_event
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.strategy.recall import RecallArmSelector


@pytest.mark.asyncio
async def test_approve_knowledge_add_writes_pending_not_official(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    engine = ProposalEngine(store=store)
    pid = "a" * 32
    engine._write_proposal_yaml({
        "id": pid,
        "project_id": "p1",
        "slot": "knowledge",
        "target_ref": "new-norm",
        "action": "add",
        "status": "approved",
        "payload": {
            "after": {
                "content_type": "convention",
                "title": "列出列名",
                "content": "查询必须显式写列名",
            }
        },
    })
    assert await engine.approve(pid) is True
    official = list((store.rsi_dir / "memory" / "conventions").glob("*.yaml"))
    pending = list((store.rsi_dir / "memory" / "pending" / "conventions").glob("*.yaml"))
    assert official == []
    assert pending


@pytest.mark.asyncio
async def test_approve_knowledge_remove_archives_existing(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    existing = store.write(
        MemoryDoc(id="b" * 32, type="convention", title="旧约定", content="旧正文足够长"),
        dest=official_dir(store.rsi_dir, "convention") / "old--bbbbbbbb.yaml",
    )
    engine = ProposalEngine(store=store)
    pid = "c" * 32
    engine._write_proposal_yaml({
        "id": pid,
        "project_id": "p1",
        "slot": "knowledge",
        "target_ref": existing.id,
        "action": "remove",
        "status": "approved",
        "payload": {"after": {}},
    })
    assert await engine.approve(pid) is True
    assert store.read(existing.id).status == "archived"


@pytest.mark.asyncio
async def test_approve_skill_writes_pending_skill_yaml_not_home_skill_md(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    engine = ProposalEngine(store=store)
    pid = "d" * 32
    engine._write_proposal_yaml({
        "id": pid,
        "project_id": "p1",
        "slot": "skill",
        "target_ref": "list-columns",
        "action": "add",
        "status": "approved",
        "payload": {"after": {"skill_md": "必须写列名", "description": "列名技能"}},
    })
    assert await engine.approve(pid) is True
    pending = list((store.rsi_dir / "memory" / "pending" / "skills").rglob("*.yaml"))
    home_md = list((tmp_path / ".rsi-home").rglob("SKILL.md")) if (tmp_path / ".rsi-home").exists() else []
    assert pending
    dumped = yaml.safe_load(pending[0].read_text(encoding="utf-8"))
    assert dumped["type"] == "skill"
    assert "必须写列名" in dumped["content"]
    assert home_md == []


@pytest.mark.asyncio
async def test_list_empty_proposals_and_snapshots_without_sqlite(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    (store.rsi_dir / "state" / "proposals").mkdir(parents=True)
    (store.rsi_dir / "state" / "snapshots").mkdir(parents=True)

    engine = ProposalEngine(store=store)
    snaps = SnapshotStore(store=store)

    assert await engine.list_proposals("p1") == []
    assert await snaps.list("p1") == []


@pytest.mark.asyncio
async def test_arm_pick_seeds_and_reads_arms_yaml(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    arm = await RecallArmSelector(store=store).pick("p1")
    path = store.rsi_dir / "state" / "arms.yaml"
    assert path.is_file()
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = payload["arms"] if isinstance(payload, dict) else payload
    names = {r.get("name") or r.get("strategy_name") for r in rows if (r.get("intent") or "recall") == "recall"}
    assert arm.name in names
    assert arm.name.startswith("recall-")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_arms(rsi_dir: Path, rows: list) -> Path:
    path = rsi_dir / "state" / "arms.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"arms": rows}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_snapshot_capture_switch_export_without_sqlite(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    _write_arms(store.rsi_dir, [{
        "id": "arm1",
        "name": "recall-balanced",
        "intent": "recall",
        "top_n": 5,
        "threshold": 0.6,
        "alpha": 1.0,
        "beta": 1.0,
        "active": True,
        "params": {"top_n": 5, "threshold": 0.6},
    }])

    snaps = SnapshotStore(store=store)
    version = await snaps.capture("p1")
    snap_path = store.rsi_dir / "state" / "snapshots" / str(version) / "snapshot.yaml"
    payload = yaml.safe_load(snap_path.read_text(encoding="utf-8"))
    strategy_slot = (payload.get("slots") or {}).get("strategy")
    assert strategy_slot, "capture must persist a non-empty strategy slot"
    values = strategy_slot.get("value") if isinstance(strategy_slot, dict) else strategy_slot
    assert values, "strategy slot value must include arms.yaml rows"

    _write_arms(store.rsi_dir, [{
        "id": "arm1",
        "name": "recall-balanced",
        "intent": "recall",
        "top_n": 99,
        "threshold": 0.1,
        "params": {"top_n": 99, "threshold": 0.1},
        "active": True,
    }])
    assert await snaps.switch("p1", version) is True
    restored = yaml.safe_load((store.rsi_dir / "state" / "arms.yaml").read_text(encoding="utf-8"))
    rows = restored["arms"] if isinstance(restored, dict) else restored
    assert rows[0]["top_n"] == 5
    assert (rows[0].get("params") or {}).get("top_n") == 5

    out = tmp_path / "bundle"
    exported = await snaps.export_bundle("p1", version, out)
    assert exported == out
    assert out.is_dir()
    assert (out / "genome.json").is_file()
    assert not list(out.rglob("*.db"))
    assert not list(out.rglob("rsi.db"))


@pytest.mark.asyncio
async def test_generate_from_rejected_events_writes_proposal_yaml(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    (store.rsi_dir / "state" / "proposals").mkdir(parents=True)
    doc = "e" * 32
    ts = _now_iso()
    for i in range(3):
        append_event(store.rsi_dir, {
            "id": f"r{i}", "kind": "recall", "token": f"tok-g{i}", "ts": ts,
            "task": "how to query", "retrieved": [doc], "arm": "recall-generous",
            "project_id": "p1", "status": "success",
        })
        append_event(store.rsi_dir, {
            "id": f"f{i}", "kind": "feedback", "token": f"tok-g{i}", "ts": ts,
            "action": "rejected", "comment": "不要用 SELECT *", "rating": 1,
        })

    ids = await ProposalEngine(store=store).generate("p1")
    assert ids, "3 rejected events must produce at least one proposal"
    dest = store.rsi_dir / "state" / "proposals" / f"{ids[0]}.yaml"
    assert dest.is_file()
    row = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert row["status"] == "proposed"
    assert row["project_id"] == "p1"
    assert row["slot"]
    assert row["action"]
    assert row.get("payload") is not None
    assert row.get("created_at")


@pytest.mark.asyncio
async def test_approve_applies_strategy_payload_to_arms_yaml(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    _write_arms(store.rsi_dir, [{
        "id": "arm1",
        "name": "recall-balanced",
        "intent": "recall",
        "top_n": 5,
        "threshold": 0.6,
        "params": {"top_n": 5, "threshold": 0.6},
        "active": True,
    }])
    pid = "a" * 32
    dest = store.rsi_dir / "state" / "proposals"
    dest.mkdir(parents=True)
    (dest / f"{pid}.yaml").write_text(yaml.safe_dump({
        "id": pid,
        "project_id": "p1",
        "slot": "strategy",
        "target_ref": "recall-balanced",
        "action": "modify",
        "payload": {"after": {"params": {"top_n": 8, "threshold": 0.4}}},
        "evidence": {},
        "status": "approved",
        "created_at": _now_iso(),
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")

    assert await ProposalEngine(store=store).approve(pid) is True
    restored = yaml.safe_load((store.rsi_dir / "state" / "arms.yaml").read_text(encoding="utf-8"))
    rows = restored["arms"] if isinstance(restored, dict) else restored
    arm = next(r for r in rows if (r.get("name") or r.get("strategy_name")) == "recall-balanced")
    params = arm.get("params") or {}
    assert arm.get("top_n") == 8 or params.get("top_n") == 8
    assert arm.get("threshold") == 0.4 or params.get("threshold") == 0.4
