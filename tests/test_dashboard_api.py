"""大屏钻取与批量审批：详情/列表/召回事件读接口 + POST /api/review 写链路。"""

import json
import urllib.error
import urllib.request

import pytest
import yaml

from rsi_boot.dashboard.api import memory_detail, memory_list, recall_events
from rsi_boot.dashboard.server import close_dashboard, open_dashboard
from rsi_boot.dashboard.snapshot import build_snapshot


@pytest.fixture(autouse=True)
def _dashboard_down():
    close_dashboard()
    yield
    close_dashboard()


def _mem(rsi, rel: str, **fields) -> None:
    path = rsi / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(fields, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _base_url(info):
    return info["url"].rsplit("/", 1)[0]


def _get(url: str):
    with urllib.request.urlopen(url, timeout=3) as resp:
        return resp.status, json.loads(resp.read())


def _post(url: str, payload) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


@pytest.fixture
def rsi(tmp_path):
    root = tmp_path / ".rsi"
    _mem(
        root, "memory/conventions/naming.yaml",
        id="a" * 32, type="convention", title="用 snake_case", domain="code",
        content="函数与变量统一 snake_case。", source="bootstrap", tags=["style"],
        created_at="2026-10-01T00:00:00Z",
    )
    _mem(
        root, "memory/pending/conventions/no-print.yaml",
        id="b" * 32, type="convention", title="禁止 print 调试", domain="code",
        content="提交前删掉 print。", created_at="2026-10-02T00:00:00Z",
    )
    _mem(
        root, "memory/pending/prohibitions/no-star.yaml",
        id="c" * 32, type="prohibition", title="不要 SELECT *", domain="sql",
        content="显式列出列名。", created_at="2026-10-03T00:00:00Z",
    )
    logs = root / "logs"
    logs.mkdir(parents=True)
    (logs / "events.jsonl").write_text(
        "\n".join([
            json.dumps({"ts": "2026-10-02T01:00:00Z", "kind": "recall", "task": "命名规范",
                        "retrieved": ["a" * 32, "f" * 32]}, ensure_ascii=False),
            json.dumps({"ts": "2026-10-03T01:00:00Z", "kind": "feedback", "action": "accepted"}),
        ]) + "\n",
        encoding="utf-8",
    )
    return root


def test_pending_items_carry_id(rsi):
    snap = build_snapshot(rsi)
    ids = {item["id"] for item in snap["pendingItems"]}
    assert ids == {"b" * 32, "c" * 32}


def test_memory_detail_full_text_and_metadata(rsi):
    doc = memory_detail(rsi, "b" * 32)
    assert doc is not None
    assert doc["content"] == "提交前删掉 print。"
    assert doc["status"] == "pending_review"
    assert doc["category"] == "conventions"
    assert doc["path"].startswith("memory/pending/")
    active = memory_detail(rsi, "a" * 32)
    assert active["source"] == "bootstrap"
    # 非法 / 不存在的 id
    assert memory_detail(rsi, "../../etc") is None
    assert memory_detail(rsi, "0" * 32) is None


def test_memory_list_filters(rsi):
    all_rows = memory_list(rsi)
    assert len(all_rows) == 3
    conv = memory_list(rsi, category="conventions")
    assert {row["id"] for row in conv} == {"a" * 32, "b" * 32}
    pending = memory_list(rsi, status="pending_review")
    assert {row["id"] for row in pending} == {"b" * 32, "c" * 32}
    assert all(row["status"] == "pending_review" for row in pending)
    by_domain = memory_list(rsi, domain="sql")
    assert [row["id"] for row in by_domain] == ["c" * 32]


def test_event_list_filters_by_kind(rsi):
    from rsi_boot.dashboard.api import event_list

    feedback = event_list(rsi, "feedback")
    assert len(feedback) == 1
    assert feedback[0]["action"] == "accepted"
    assert all(ev["kind"] == "feedback" for ev in feedback)
    everything = event_list(rsi)
    assert {ev["kind"] for ev in everything} == {"recall", "feedback"}


def test_recall_events_resolve_titles(rsi):
    events = recall_events(rsi)
    assert len(events) == 1
    hits = {hit["id"]: hit for hit in events[0]["hits"]}
    assert hits["a" * 32]["title"] == "用 snake_case"
    assert hits["a" * 32]["missing"] is False
    assert hits["f" * 32]["missing"] is True


def test_http_get_endpoints(rsi):
    info = open_dashboard(rsi, port=0)
    base = _base_url(info)
    status, detail = _get(base + "/api/memory/" + "a" * 32)
    assert status == 200
    assert detail["title"] == "用 snake_case"
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(base + "/api/memory/" + "0" * 32, timeout=3)
    assert err.value.code == 404
    status, listing = _get(base + "/api/memories?category=prohibitions")
    assert [row["title"] for row in listing["items"]] == ["不要 SELECT *"]
    status, recalls = _get(base + "/api/recalls")
    assert status == 200
    assert recalls["events"][0]["task"] == "命名规范"
    status, events = _get(base + "/api/events?kind=feedback")
    assert status == 200
    assert [ev["action"] for ev in events["events"]] == ["accepted"]
    status, by_domain = _get(base + "/api/memories?domain=sql")
    assert [row["title"] for row in by_domain["items"]] == ["不要 SELECT *"]


def test_post_review_approve_moves_files(rsi):
    info = open_dashboard(rsi, port=0)
    base = _base_url(info)
    status, res = _post(base + "/api/review", {"ids": ["b" * 32, "c" * 32], "action": "approve"})
    assert status == 200
    assert res["processed"] == 2
    assert res["new_status"] == "active"
    assert not (rsi / "memory/pending/conventions/no-print.yaml").exists()
    assert not (rsi / "memory/pending/prohibitions/no-star.yaml").exists()
    # 搬家后文件名按标题 slug 化，以详情接口回读的路径为准
    assert memory_detail(rsi, "b" * 32)["path"].startswith("memory/conventions/")
    assert memory_detail(rsi, "c" * 32)["path"].startswith("memory/prohibitions/")
    snap = build_snapshot(rsi)
    assert snap["meta"]["pendingReview"] == 0
    # 审批写了 review 事件日志
    events = (rsi / "logs").glob("events*.jsonl")
    kinds = [
        json.loads(line)
        for path in events
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    review_events = [e for e in kinds if e.get("kind") == "review" and e.get("action") == "approve"]
    assert review_events and review_events[-1]["processed"] == 2


def test_post_review_reject_archives(rsi):
    info = open_dashboard(rsi, port=0)
    base = _base_url(info)
    status, res = _post(base + "/api/review", {"ids": ["c" * 32], "action": "reject"})
    assert status == 200
    assert res["processed"] == 1
    assert not (rsi / "memory/pending/prohibitions/no-star.yaml").exists()
    assert memory_detail(rsi, "c" * 32)["path"].startswith("memory/archive/")


def test_post_review_validation(rsi):
    info = open_dashboard(rsi, port=0)
    base = _base_url(info)
    status, _ = _post(base + "/api/review", {"ids": [], "action": "approve"})
    assert status == 400
    status, _ = _post(base + "/api/review", {"ids": ["b" * 32], "action": "nuke"})
    assert status == 400
    # 已 active 的条目不可再审批：processed=0
    status, res = _post(base + "/api/review", {"ids": ["a" * 32], "action": "approve"})
    assert status == 200
    assert res["processed"] == 0
