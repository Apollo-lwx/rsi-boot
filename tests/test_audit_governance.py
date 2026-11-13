"""决策日志三维（借鉴 OpenMausBot decision log）：actor + 保留窗 + 导出中和。

验收（方案任务 1.3）：
- 审计行含 actor（宿主标识 + 会话标识）；审批 review 事件带 actor.host；
- 超保留窗删除、超滚动阈值按月归档、dry-run 不落盘、保留窗可配；
- 导出 CSV：公式注入单元格被 ' 中和、密钥样式被脱敏、换行压平。

变异点：去掉 _csv_safe 的前缀中和 → 公式用例红；prune 不判 retention → 删除用例红。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from rsi_boot.learning.audit_store import (
    audit_finish,
    audit_report,
    audit_start,
    export_audit_csv,
    prune_audit_files,
)

_NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def _set_mtime(path: Path, days_ago: float) -> None:
    ts = (_NOW - timedelta(days=days_ago)).timestamp()
    os.utime(path, (ts, ts))


def _write_audit_file(rsi_dir: Path, name: str, body: dict, days_ago: float) -> Path:
    dest = rsi_dir / "audit" / "session" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    import yaml as _yaml

    dest.write_text(_yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    _set_mtime(dest, days_ago)
    return dest


# ---------- actor ----------


def test_audit_session_carries_actor(store, monkeypatch):
    monkeypatch.setenv("RSI_HOST", "cursor")
    result = audit_start(store, {"scope": "session"}, "zh")
    assert result["status"] == "success"
    actor = result["data"]["actor"]
    assert actor["host"] == "cursor"
    assert len(actor["session_id"]) == 12

    report = audit_report(store, {"scope": "session", "overall": "pass"}, "zh")
    body = yaml.safe_load((store.rsi_dir / report["data"]["path"]).read_text(encoding="utf-8"))
    assert body["actor"]["host"] == "cursor"
    assert body["actor"]["session_id"] == actor["session_id"]
    audit_finish(store, {"scope": "session"}, "zh")


def test_actor_host_defaults_unknown(store, monkeypatch):
    monkeypatch.delenv("RSI_HOST", raising=False)
    result = audit_start(store, {"scope": "session"}, "zh")
    assert result["data"]["actor"]["host"] == "unknown"
    audit_finish(store, {"scope": "session"}, "zh")


def test_review_event_carries_actor_host(store, monkeypatch):
    from rsi_boot.api.tools.knowledge_review_tool import _log_review

    monkeypatch.setenv("RSI_HOST", "qoder")
    _log_review(store, action="approve", ids=["a" * 32], processed=1)
    lines = (store.rsi_dir / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[-1])
    assert event["kind"] == "review"
    assert event["actor"]["host"] == "qoder"


# ---------- 按月滚动 + 保留窗 ----------


def test_prune_deletes_beyond_retention(store):
    old = _write_audit_file(store.rsi_dir, "audit-20260501T000000Z.yaml", {"overall": "pass"}, days_ago=197)
    result = prune_audit_files(store.rsi_dir, retention_days=180, now=_NOW)
    assert result["pruned"] == 1
    assert not old.exists()


def test_prune_rolls_into_months(store):
    mid = _write_audit_file(store.rsi_dir, "audit-20260920T000000Z.yaml", {"overall": "fail"}, days_ago=55)
    fresh = _write_audit_file(store.rsi_dir, "audit-20261113T000000Z.yaml", {"overall": "pass"}, days_ago=1)
    result = prune_audit_files(store.rsi_dir, retention_days=180, archive_after_days=30, now=_NOW)
    assert result["archived"] == 1 and result["pruned"] == 0
    assert not mid.exists()
    rolled = store.rsi_dir / "audit" / "_months" / "2026-09" / mid.name
    assert rolled.is_file()
    assert fresh.is_file()  # 新文件不动


def test_prune_dry_run_writes_nothing(store):
    _write_audit_file(store.rsi_dir, "audit-20260501T000000Z.yaml", {"overall": "pass"}, days_ago=197)
    result = prune_audit_files(store.rsi_dir, retention_days=180, now=_NOW, dry_run=True)
    assert result["pruned"] == 1
    assert (store.rsi_dir / "audit" / "session" / "audit-20260501T000000Z.yaml").is_file()


def test_prune_months_dir_not_double_processed(store):
    """_months 内的文件不再二次归档，但仍受保留窗约束。"""
    month_dir = store.rsi_dir / "audit" / "_months" / "2026-04"
    month_dir.mkdir(parents=True)
    inside = month_dir / "audit-20260401T000000Z.yaml"
    inside.write_text("overall: pass\n", encoding="utf-8")
    _set_mtime(inside, 200)
    result = prune_audit_files(store.rsi_dir, retention_days=180, now=_NOW)
    assert result["pruned"] == 1 and result["archived"] == 0
    assert not inside.exists()


# ---------- 导出：公式注入中和 + 脱敏 ----------


def test_export_neutralizes_formula_and_masks_secret(store):
    _write_audit_file(store.rsi_dir, "audit-20261113T120000Z.yaml", {
        "scope": "session",
        "overall": "fail",
        "findings": ["=HYPERLINK(\"http://evil.sh\",\"x\")"],
        "actor": {"host": "cursor", "session_id": "abc123"},
    }, days_ago=0)
    # scope 以公式前缀开头 → 单元格被 ' 中和
    _write_audit_file(store.rsi_dir, "audit-20261113T130000Z.yaml", {
        "scope": "=cmd|' /C calc", "overall": "pass",
        "actor": {"host": "sk-abcdefghijklmnopqrstuvwxyz", "session_id": "s1"},
    }, days_ago=0)

    dest = store.rsi_dir / "out" / "audit.csv"
    result = export_audit_csv(store.rsi_dir, dest)
    assert result["rows"] == 2
    text = dest.read_text(encoding="utf-8-sig")
    assert "'=cmd|' /C calc" in text  # 公式注入被中和
    assert "sk-****" in text  # 密钥样式被脱敏
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in text
    assert "HYPERLINK" in text  # findings 内容保留在 detail（非首字符不构成公式）


def test_export_review_rows_carry_actor(store, monkeypatch):
    from rsi_boot.api.tools.knowledge_review_tool import _log_review

    monkeypatch.setenv("RSI_HOST", "codex")
    _log_review(store, action="approve", ids=["b" * 32], processed=3)
    dest = store.rsi_dir / "out" / "audit.csv"
    result = export_audit_csv(store.rsi_dir, dest)
    assert result["rows"] == 1
    text = dest.read_text(encoding="utf-8-sig")
    assert "codex" in text
    assert "approve" in text
