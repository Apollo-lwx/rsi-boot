"""P3.1/P3.2 收尾测试（§4.2 映射表）：copied/referenced 反馈 → 检索命中标签兴趣 +1"""

import uuid
from datetime import datetime, timezone

from rsi_boot.feedback.implicit_tracker import FeedbackWorker, ImplicitEvent
from rsi_boot.memory.logstore import append_event
from rsi_boot.services.profile_service import ProfileService


def _seed_log(store, *, token, tags=None):
    append_event(store.rsi_dir, {
        "id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "recall",
        "token": token,
        "status": "success",
        "user_id": "u1",
        "project_id": "p1",
        "task": "查询",
        "retrieved": [],
        "retrieved_legacy_tags": list(tags or []),
    })


async def test_copied_boosts_retrieved_tag_interests(store):
    profiles = ProfileService(store)
    token = "tok-" + uuid.uuid4().hex[:8]
    _seed_log(store, token=token, tags=["缓存", "backend"])

    worker = FeedbackWorker(store=store, profiles=profiles)
    await worker._process(ImplicitEvent(feedback_token=token, action="copied"))

    profile = await profiles.get("u1", "p1")
    counts = profile.project_insights.get("_interest_counts", {})
    assert counts.get("缓存") == 1
    assert counts.get("backend") == 1
    assert "缓存" in profile.knowledge_interests


async def test_referenced_without_tags_noop(store):
    profiles = ProfileService(store)
    token = "tok-" + uuid.uuid4().hex[:8]
    _seed_log(store, token=token)

    worker = FeedbackWorker(store=store, profiles=profiles)
    await worker._process(ImplicitEvent(feedback_token=token, action="referenced"))

    profile = await profiles.get("u1", "p1")
    assert profile.project_insights.get("_interest_counts", {}) == {}


async def test_ignored_does_not_boost(store):
    profiles = ProfileService(store)
    token = "tok-" + uuid.uuid4().hex[:8]
    _seed_log(store, token=token, tags=["tag1"])

    worker = FeedbackWorker(store=store, profiles=profiles)
    await worker._process(ImplicitEvent(feedback_token=token, action="ignored"))

    profile = await profiles.get("u1", "p1")
    assert profile.project_insights.get("_interest_counts", {}) == {}
