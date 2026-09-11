"""P3.1/P3.2 收尾测试（§4.2 映射表）：copied/referenced 反馈 → 检索命中标签兴趣 +1"""

import uuid

import pytest

from rsi_boot.core.models import RSIRequest
from rsi_boot.feedback.implicit_tracker import FeedbackWorker, ImplicitEvent
from rsi_boot.services.log_service import LogService
from rsi_boot.services.profile_service import ProfileService


async def test_copied_boosts_retrieved_tag_interests(db):
    logs = LogService(db)
    profiles = ProfileService(db)
    req = RSIRequest(user_id="u1", project_id="p1", raw_input="缓存怎么用？")
    token = "tok-" + uuid.uuid4().hex[:8]
    log_id = await logs.insert_pending(req, token)
    await logs.finalize(
        log_id, status="success", intent="explain", response_excerpt="回答",
        retrieved_tags=["缓存", "backend"],
    )

    worker = FeedbackWorker(db, profiles=profiles)
    await worker._process(ImplicitEvent(feedback_token=token, action="copied"))

    profile = await profiles.get("u1", "p1")
    counts = profile.project_insights.get("_interest_counts", {})
    assert counts.get("缓存") == 1
    assert counts.get("backend") == 1
    assert "缓存" in profile.knowledge_interests


async def test_referenced_without_tags_noop(db):
    logs = LogService(db)
    profiles = ProfileService(db)
    req = RSIRequest(user_id="u1", project_id="p1", raw_input="无检索命中的查询")
    token = "tok-" + uuid.uuid4().hex[:8]
    log_id = await logs.insert_pending(req, token)
    await logs.finalize(log_id, status="success", intent="explain")

    worker = FeedbackWorker(db, profiles=profiles)
    await worker._process(ImplicitEvent(feedback_token=token, action="referenced"))

    profile = await profiles.get("u1", "p1")
    assert profile.project_insights.get("_interest_counts", {}) == {}


async def test_ignored_does_not_boost(db):
    logs = LogService(db)
    profiles = ProfileService(db)
    req = RSIRequest(user_id="u1", project_id="p1", raw_input="查询")
    token = "tok-" + uuid.uuid4().hex[:8]
    log_id = await logs.insert_pending(req, token)
    await logs.finalize(
        log_id, status="success", intent="explain", retrieved_tags=["tag1"],
    )

    worker = FeedbackWorker(db, profiles=profiles)
    await worker._process(ImplicitEvent(feedback_token=token, action="ignored"))

    profile = await profiles.get("u1", "p1")
    assert profile.project_insights.get("_interest_counts", {}) == {}
