"""P3.3 Thompson Sampling 策略选择测试（§3.3）：采样选择 / 曝光计数 / 缓存 / 衰减淘汰"""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from rsi_boot.scheduler.tasks import tune_strategies
from rsi_boot.strategy.engine import StrategyEngine


async def _add_strategy(db, name, intent="debug", project="p1", role=None,
                        alpha=1.0, beta=1.0, exposure=0, active=1):
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()
    sid = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO strategy_configs (id, project_id, intent, role, strategy_name, model_name,"
        " alpha, beta, exposure_count, is_active, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 'mock', ?, ?, ?, ?, ?, ?)",
        (sid, project, intent, role, name, alpha, beta, exposure, active, now, now),
    )
    await conn.commit()
    return sid


async def _get(db, sid):
    conn = await db.connect()
    async with conn.execute(
        "SELECT alpha, beta, exposure_count, is_active FROM strategy_configs WHERE id = ?", (sid,)
    ) as cur:
        return dict(await cur.fetchone())


async def test_single_candidate_deterministic(db, base_config):
    engine = StrategyEngine(db, base_config)
    await _add_strategy(db, "only-one")
    for _ in range(5):
        decision = await engine.decide("p1", "debug")
        assert decision.strategy_name == "only-one"


async def test_thompson_prefers_strong_arm(db, base_config):
    """高 alpha 臂应在多数采样中胜出（统计断言，非确定单点）"""
    engine = StrategyEngine(db, base_config)
    await _add_strategy(db, "weak", alpha=1.0, beta=9.0)    # 后验均值 0.1
    await _add_strategy(db, "strong", alpha=9.0, beta=1.0)  # 后验均值 0.9
    picks = {"weak": 0, "strong": 0}
    for _ in range(200):
        engine.invalidate_cache()  # 防缓存影响采样独立性（缓存内容相同，仅为严谨）
        decision = await engine.decide("p1", "debug")
        picks[decision.strategy_name] += 1
    assert picks["strong"] > 150  # 期望 ~90%+


async def test_exposure_incremented(db, base_config):
    engine = StrategyEngine(db, base_config)
    sid = await _add_strategy(db, "s1")
    await engine.decide("p1", "debug")
    await asyncio.sleep(0.1)  # 曝光为后台任务
    row = await _get(db, sid)
    assert row["exposure_count"] == 1


async def test_role_pool_preferred(db, base_config):
    engine = StrategyEngine(db, base_config)
    await _add_strategy(db, "generic", role=None)
    await _add_strategy(db, "role-specific", role="test")
    decision = await engine.decide("p1", "debug", role="test")
    assert decision.strategy_name == "role-specific"
    # 无 role 请求走通用池
    decision = await engine.decide("p1", "debug", role=None)
    assert decision.strategy_name == "generic"


async def test_fallback_default_when_no_strategy(db, base_config):
    engine = StrategyEngine(db, base_config)
    decision = await engine.decide("p1", "explain")
    assert decision.strategy_name == "default"
    assert decision.strategy_id is None


async def test_cache_avoids_repeat_queries(db, base_config, monkeypatch):
    engine = StrategyEngine(db, base_config)
    await _add_strategy(db, "s1")
    await engine.decide("p1", "debug")
    # 第二次命中缓存：删库行后仍返回缓存结果
    conn = await db.connect()
    await conn.execute("DELETE FROM strategy_configs")
    await conn.commit()
    decision = await engine.decide("p1", "debug")
    assert decision.strategy_name == "s1"
    engine.invalidate_cache()
    decision = await engine.decide("p1", "debug")
    assert decision.strategy_name == "default"


# ---------- 周调优：衰减 + 淘汰 ----------


async def test_tune_decays_toward_prior(db):
    sid = await _add_strategy(db, "s1", alpha=11.0, beta=3.0)
    result = await tune_strategies(db)
    row = await _get(db, sid)
    assert row["alpha"] == pytest.approx(1 + 10 * 0.9)   # 10.0
    assert row["beta"] == pytest.approx(1 + 2 * 0.9)     # 2.8
    assert result["decayed"] == 1


async def test_tune_eliminates_low_acceptance(db):
    # 曝光 150，采纳率 = 1/(1+9) = 10% < 20% → 淘汰
    sid = await _add_strategy(db, "bad", alpha=1.0, beta=9.0, exposure=150)
    keep = await _add_strategy(db, "good", alpha=9.0, beta=1.0, exposure=150)
    low_exposure = await _add_strategy(db, "new", alpha=1.0, beta=9.0, exposure=10)
    result = await tune_strategies(db)
    assert (await _get(db, sid))["is_active"] == 0
    assert (await _get(db, keep))["is_active"] == 1
    assert (await _get(db, low_exposure))["is_active"] == 1  # 曝光不足不淘汰
    assert result["eliminated"] == 1
