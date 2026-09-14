"""File-backend review/snapshots/arms: list empty state/ and seed arms.yaml."""

import pytest
import yaml

from rsi_boot.learning.proposal_engine import ProposalEngine
from rsi_boot.learning.snapshot_store import SnapshotStore
from rsi_boot.memory.store import MemoryStore
from rsi_boot.strategy.recall import RecallArmSelector


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
