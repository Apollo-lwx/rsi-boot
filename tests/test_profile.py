"""P2.4 用户画像测试（§3.8）：读写穿缓存 / 合并 / 运行时增量 / 离线衰减重建"""

from rsi_boot.core.models import KnowledgeItem, UserProfile
from rsi_boot.services.profile_service import ProfileService


async def _insert_log(db, user_id, intent=None, model=None, action=None, created_at=None):
    import uuid
    from datetime import datetime, timezone

    now = created_at or datetime.now(timezone.utc).isoformat()
    conn = await db.connect()
    rid = uuid.uuid4().hex
    await conn.execute(
        """
        INSERT INTO interaction_logs
            (id, request_id, user_id, project_id, raw_input, intent, model_name,
             feedback_action, latency_ms, status, feedback_token, created_at)
        VALUES (?, ?, ?, 'p1', 'input', ?, ?, ?, 10, 'success', ?, ?)
        """,
        (rid, rid, user_id, intent, model, action, f"tok-{rid}", now),
    )
    await conn.commit()


async def test_default_profile_cold_start(db):
    svc = ProfileService(db)
    profile = await svc.get("u1", "p1")
    assert profile.user_id == "u1"
    assert profile.preferences.response_lang == "zh"
    assert profile.frequently_used_intents == {}


async def test_upsert_and_get_roundtrip(db):
    svc = ProfileService(db)
    profile = UserProfile(user_id="u1", project_id="p1", expertise=["python", "sqlite"])
    await svc.upsert(profile)
    loaded = await svc.get("u1", "p1")
    assert loaded.expertise == ["python", "sqlite"]


async def test_write_through_invalidates_cache(db):
    svc = ProfileService(db)
    await svc.upsert(UserProfile(user_id="u1", project_id="p1", expertise=["python"]))
    first = await svc.get("u1", "p1")  # 回填缓存
    assert first.expertise == ["python"]

    await svc.upsert(UserProfile(user_id="u1", project_id="p1", expertise=["go"]))
    second = await svc.get("u1", "p1")
    assert second.expertise == ["go"]  # 写穿后缓存已失效，读到新值


async def test_global_and_project_merge(db):
    svc = ProfileService(db)
    g = UserProfile(user_id="u1", project_id=None, expertise=["python"], history_summary="全局摘要")
    g.preferences.detail_level = "detailed"
    await svc.upsert(g)
    p = UserProfile(user_id="u1", project_id="p1", expertise=["fastapi"])
    await svc.upsert(p)

    merged = await svc.get("u1", "p1")
    assert merged.expertise == ["fastapi", "python"]  # 项目优先，全局补充
    assert merged.preferences.detail_level == "detailed"  # 项目未设置回落全局
    assert merged.history_summary == "全局摘要"


async def test_record_intent_increments(db):
    svc = ProfileService(db)
    await svc.record_intent("u1", "p1", "debug")
    await svc.record_intent("u1", "p1", "debug")
    await svc.record_intent("u1", "p1", "code_gen")
    profile = await svc.get("u1", "p1")
    assert profile.frequently_used_intents == {"debug": 2, "code_gen": 1}


async def test_record_retrieval_hits_top20(db):
    svc = ProfileService(db)
    items = [
        KnowledgeItem(project_id="p1", title="t", content="c", domain="deploy", tags=["docker", "ci"]),
        KnowledgeItem(project_id="p1", title="t2", content="c2", domain="deploy", tags=["k8s"]),
    ]
    await svc.record_retrieval_hits("u1", "p1", items)
    profile = await svc.get("u1", "p1")
    assert profile.knowledge_interests[0] == "deploy"  # 计数 2 排第一
    assert set(profile.knowledge_interests) == {"deploy", "docker", "ci", "k8s"}


async def test_rebuild_decays_intent_counts(db):
    from datetime import timedelta
    from datetime import datetime, timezone

    svc = ProfileService(db)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()  # 半衰期 30 天 → 权重 0.5
    await _insert_log(db, "u1", intent="debug", created_at=old)
    await _insert_log(db, "u1", intent="debug", created_at=old)
    await _insert_log(db, "u1", intent="code_gen")  # 新事件权重 1.0

    profile = await svc.rebuild("u1", "p1")
    # 两条 30 天前的 debug：2 × 0.5 = 1；一条新 code_gen：1.0
    assert profile.frequently_used_intents.get("debug") == 1
    assert profile.frequently_used_intents.get("code_gen") == 1


async def test_rebuild_preferred_models_min_exposure(db):
    svc = ProfileService(db)
    # gpt-a：25 次曝光 20 次采纳；gpt-b：5 次曝光全采纳（< 20 不纳入）
    for i in range(25):
        await _insert_log(db, "u1", model="gpt-a", action="accepted" if i < 20 else None)
    for _ in range(5):
        await _insert_log(db, "u1", model="gpt-b", action="accepted")

    profile = await svc.rebuild("u1", "p1")
    assert profile.preferred_models == ["gpt-a"]


async def test_rebuild_preserves_preferences(db):
    """离线任务不改写 preferences（§3.8：不猜测用户偏好）"""
    svc = ProfileService(db)
    p = UserProfile(user_id="u1", project_id="p1")
    p.preferences.code_style = "snake_case"
    await svc.upsert(p)
    await _insert_log(db, "u1", intent="debug")

    profile = await svc.rebuild("u1", "p1")
    assert profile.preferences.code_style == "snake_case"
