"""Runtime dual-path services must not import SQLiteClient. Migrate and data/ keep it."""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1] / "src" / "rsi_boot"

_FORBIDDEN = (
    "injector/conflict.py",
    "injector/rule_injector.py",
    "feedback/implicit_tracker.py",
    "learning/knowledge_extractor.py",
    "learning/proposal_engine.py",
    "learning/snapshot_store.py",
    "services/decision_queue.py",
    "services/knowledge_service.py",
    "services/log_service.py",
    "services/profile_service.py",
    "services/recall_service.py",
    "services/stats_service.py",
    "strategy/recall.py",
)


@pytest.mark.parametrize("rel", _FORBIDDEN)
def test_runtime_service_does_not_import_sqlite_client(rel):
    text = (_ROOT / rel).read_text(encoding="utf-8")
    assert "SQLiteClient" not in text
    assert "from ..data.sqlite" not in text
    assert "from rsi_boot.data.sqlite" not in text
