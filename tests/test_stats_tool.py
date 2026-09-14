"""rsi_stats 记忆使用统计测试（Spec v3.0 §6，A6）——触达/采纳/禁止项遵循/提案通过率。

v3.0 口径：主链路零模型调用，不再统计 token/成本；数据来自 events.jsonl
（kind='recall'）、artifacts.yaml 与 harness 提案 YAML。
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from rsi_boot.api.tools import stats_tool
from rsi_boot.memory.logstore import append_event
from rsi_boot.services.stats_service import StatsService


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _seed(store, *, intent="recall", arm="balanced", feedback_action=None, rating=None,
          created_at=None, project_id="p1", status="success", excerpt="命中：经验A"):
    """造一条召回事件（kind='recall'，零 token）"""
    token = uuid.uuid4().hex
    ts = created_at or _iso(datetime.now(timezone.utc))
    append_event(store.rsi_dir, {
        "id": uuid.uuid4().hex,
        "kind": "recall" if intent == "recall" else intent,
        "intent": intent,
        "arm": arm,
        "status": status,
        "ts": ts,
        "action": feedback_action,
        "rating": rating,
        "excerpt": excerpt,
        "project_id": project_id,
        "token": token,
        "latency_ms": 25,
        "retrieved": [],
    })


# ---------- StatsService ----------


async def test_summary_month_window(store):
    stats = StatsService(store)
    now = datetime.now(timezone.utc)
    _seed(store, feedback_action="accepted")
    _seed(store, feedback_action="rejected", created_at=_iso(now - timedelta(days=10)))
    _seed(store, created_at=_iso(now - timedelta(days=40)))  # 窗口外
    _seed(store, status="pending")  # 终态才统计
    _seed(store, intent="debug")  # 非 recall 行不计入

    result = await stats.summary("month")
    assert result["recalls"] == 2
    assert result["adoption_rate"] == pytest.approx(0.5)  # 1 正 / 2 反馈
    assert result["avg_recall_latency_ms"] == pytest.approx(25.0)


async def test_summary_day_excludes_yesterday(store):
    stats = StatsService(store)
    now = datetime.now(timezone.utc)
    _seed(store)
    _seed(store, created_at=_iso(now - timedelta(days=1)))
    assert (await stats.summary("day"))["recalls"] == 1
    assert (await stats.summary("week"))["recalls"] == 2


async def test_summary_prohibition_compliance(store):
    """禁止项遵循率近似口径：触达含「禁止：」的召回中，负反馈占比即违反"""
    stats = StatsService(store)
    _seed(store, excerpt="命中：禁止：不要用 SELECT *", feedback_action="accepted")
    _seed(store, excerpt="命中：禁止：不要用 SELECT *", feedback_action="rejected")
    _seed(store, excerpt="命中：普通经验", feedback_action="rejected")  # 非禁止项触达不计

    result = await stats.summary("week")
    assert result["prohibition_surfaced"] == 2
    assert result["prohibition_compliance"] == pytest.approx(0.5)


async def test_grouped_by_arm(store):
    stats = StatsService(store)
    _seed(store, arm="balanced", feedback_action="accepted")
    _seed(store, arm="balanced", feedback_action="ignored")
    _seed(store, arm="generous", feedback_action="rejected")

    by_arm = await stats.grouped("week", "arm")
    assert [(g["arm"], g["recalls"]) for g in by_arm] == [("balanced", 2), ("generous", 1)]
    assert by_arm[0]["adoption_rate"] == pytest.approx(0.5)
    assert by_arm[1]["adoption_rate"] == pytest.approx(0.0)

    by_intent = await stats.grouped("week", "intent")
    assert by_intent[0]["intent"] == "recall" and by_intent[0]["recalls"] == 3


async def test_trend_granularity(store):
    stats = StatsService(store)
    now = datetime.now(timezone.utc)
    _seed(store, created_at=_iso(now))
    _seed(store, created_at=_iso(now - timedelta(days=2)))

    day_trend = await stats.trend("day")
    assert len(day_trend) == 1
    assert len(day_trend[0]["bucket"]) == 13  # day 按小时分桶 YYYY-MM-DDTHH
    assert day_trend[0]["recalls"] == 1

    week_trend = await stats.trend("week")
    assert len(week_trend) == 2
    assert all(len(t["bucket"]) == 10 for t in week_trend)  # week 按日分桶
    assert week_trend[0]["bucket"] < week_trend[1]["bucket"]  # 时间升序


async def test_project_filter(store):
    stats = StatsService(store)
    _seed(store, project_id="p1")
    _seed(store, project_id="p2")
    assert (await stats.summary("week", project_id="p1"))["recalls"] == 1
    assert (await stats.summary("week"))["recalls"] == 2


# ---------- 工具 handler ----------


async def test_tool_period_required(store):
    result = await stats_tool.handle(StatsService(store), {})
    assert result["status"] == "error"
    assert "period" in result["message"]


async def test_tool_invalid_group_by(store):
    result = await stats_tool.handle(StatsService(store), {"period": "day", "group_by": "user"})
    assert result["status"] == "error"


async def test_tool_json_payload(store):
    _seed(store, arm="conservative", feedback_action="copied")
    result = await stats_tool.handle(StatsService(store), {"period": "week", "group_by": "arm"})
    assert result["status"] == "success"
    assert result["summary"]["recalls"] == 1
    assert result["summary"]["adoption_rate"] == pytest.approx(1.0)
    assert result["groups"][0]["arm"] == "conservative"
    assert result["trend"] and result["window_start"]


async def test_tool_export_json(store, tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path))
    _seed(store)
    result = await stats_tool.handle(StatsService(store), {"period": "month", "export": True})
    assert result["status"] == "success"
    exported = tmp_path / "exports" / result["exported"].split("exports")[-1].lstrip("\\/")
    assert exported.name.startswith("rsi_stats_month_") and exported.suffix == ".json"
    payload = json.loads(exported.read_text(encoding="utf-8"))
    assert payload["summary"]["recalls"] == 1


async def test_tool_export_csv(store, tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path))
    _seed(store, arm="balanced")
    result = await stats_tool.handle(
        StatsService(store), {"period": "week", "group_by": "arm", "format": "csv"}
    )
    assert result["status"] == "success"
    content = (tmp_path / "exports").glob("*.csv").__next__().read_text(encoding="utf-8")
    assert "metric,value" in content
    assert "balanced" in content
