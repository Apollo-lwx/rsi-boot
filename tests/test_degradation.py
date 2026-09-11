"""§5.2/§5.3/§5.4 降级链路：sqlite/embedding 熔断器（3/30s）、Level 1/2 降级、
LLM full-jitter 重试、SQLite 提交 BUSY 重试（测试计划 CIR-01/02、CHA-01/02）。"""

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

from rsi_boot.common.circuit_breaker import State
from rsi_boot.core.exceptions import ModelTimeoutError
from rsi_boot.core.models import RSIRequest
from rsi_boot.data.sqlite import SQLiteClient
from rsi_boot.knowledge.embedding import EmbeddingService
from rsi_boot.knowledge.retriever import KnowledgeRetriever
from rsi_boot.model.adapter import ModelAdapter
from rsi_boot.model.providers.mock import MockProvider
from rsi_boot.orchestrator.pipeline import Pipeline
from rsi_boot.services.log_service import LogService
from rsi_boot.services.profile_service import ProfileService


def _broken_connect(db, monkeypatch, exc=None):
    """让 db.connect 持续抛运行态错误，返回调用计数器"""
    calls = {"n": 0}
    original = db.connect

    async def _raising():
        calls["n"] += 1
        raise (exc or aiosqlite.OperationalError("disk I/O error"))

    monkeypatch.setattr(db, "connect", _raising)
    return calls, original


# ---------- sqlite 熔断器（§5.2：阈值 3 / 恢复 30s） ----------


def test_sqlite_breaker_params(db):
    assert db.breaker.threshold == 3
    assert db.breaker.recovery_timeout_s == 30.0
    assert db.breaker.state is State.CLOSED  # 进程内冷启动（CIR-07）


async def test_log_insert_failsoft_and_breaker_trips(db, monkeypatch):
    logs = LogService(db)
    calls, _ = _broken_connect(db, monkeypatch)
    req = RSIRequest(user_id="u", raw_input="q")

    for _ in range(3):
        assert await logs.insert_pending(req, "tok") == ""  # 降级跳过持久化，不抛异常
    assert db.breaker.state is State.OPEN  # 连续 3 次运行态失败 → OPEN（CIR-01）

    assert await logs.insert_pending(req, "tok") == ""
    assert calls["n"] == 3  # OPEN 后 fail-fast，不再实际连接（CIR-05）


async def test_non_operational_error_not_counted(db, monkeypatch):
    logs = LogService(db)
    _broken_connect(db, monkeypatch, exc=aiosqlite.IntegrityError("UNIQUE constraint"))
    for _ in range(3):
        await logs.insert_pending(RSIRequest(user_id="u", raw_input="q"), "tok")
    assert db.breaker.state is State.CLOSED  # 业务/约束错误不计入（§5.1 口径）


async def test_finalize_noop_on_empty_log_id(db):
    await LogService(db).finalize("", status="success")  # pending 已降级 → 直接跳过


async def test_profile_get_degrades_to_default(db, monkeypatch):
    profiles = ProfileService(db)
    calls, _ = _broken_connect(db, monkeypatch)

    profile = await profiles.get("u", "p1")
    assert profile.user_id == "u" and not profile.expertise  # 默认画像，不阻塞（§3.8）
    for _ in range(2):
        profiles._cache.clear()
        await profiles.get("u", "p1")
    assert db.breaker.state is State.OPEN
    profiles._cache.clear()
    await profiles.get("u", "p1")
    assert calls["n"] == 3  # OPEN 后 fail-fast


async def test_pipeline_level2_response_still_returned(db, base_config, monkeypatch):
    """CHA-01：sqlite 不可用 → 日志/画像跳过持久化，响应照常返回（§5.4 Level 2）"""
    adapter = ModelAdapter(ModelRegistryStub(), base_config)
    pipeline = Pipeline(db, base_config, KnowledgeRetriever(db, base_config), adapter,
                        profiles=ProfileService(db))
    req = lambda: RSIRequest(user_id="u", project_id="p1", raw_input="如何配置超时", intent="howto")

    warm = await pipeline.run(req())  # 预热策略臂/画像缓存
    assert warm.status == "success"
    await pipeline.drain()

    _broken_connect(db, monkeypatch)
    degraded = await pipeline.run(req())
    await pipeline.drain()
    assert degraded.status == "success"  # 响应照常返回
    assert degraded.content[0].body == warm.content[0].body


class ModelRegistryStub:
    def get_provider(self, model_name):
        return MockProvider()


# ---------- embedding 熔断器（§5.2：阈值 3 / 恢复 30s；§5.4 Level 1） ----------


def _embedding_service():
    svc = EmbeddingService({"embedding": {"provider": "openai", "model": "m"}})
    svc._client = SimpleNamespace(embeddings=None)
    return svc


async def test_embedding_breaker_trips_and_failfast(monkeypatch):
    svc = _embedding_service()
    assert svc.breaker.threshold == 3 and svc.breaker.recovery_timeout_s == 30.0

    calls = {"n": 0}

    async def _boom(model, input):
        calls["n"] += 1
        raise RuntimeError("5xx upstream")

    svc._client.embeddings = SimpleNamespace(create=_boom)
    for _ in range(3):
        assert await svc.embed(["x"], api_key="k") == [None]  # 降级不抛异常
    assert svc.breaker.state is State.OPEN

    assert await svc.embed(["x"], api_key="k") == [None]
    assert calls["n"] == 3  # OPEN 后 fail-fast（CHA-02：检索走纯 FTS5，不拖慢主链路）


async def test_embedding_4xx_not_counted():
    svc = _embedding_service()

    class _BadRequest(Exception):
        status_code = 400

    async def _boom(model, input):
        raise _BadRequest("bad input")

    svc._client.embeddings = SimpleNamespace(create=_boom)
    for _ in range(4):
        await svc.embed(["x"], api_key="k")
    assert svc.breaker.state is State.CLOSED


# ---------- LLM 重试 full jitter（§5.3：base=1s cap=30s，最多 3 次） ----------


class _TimeoutProvider:
    name = "timeout"

    def __init__(self):
        self.calls = 0

    async def complete(self, model, messages, timeout_s):
        self.calls += 1
        raise ModelTimeoutError("slow")


async def test_llm_retry_full_jitter(base_config, monkeypatch):
    cfg = {**base_config, "model": {**base_config["model"], "max_retries": 3}}
    provider = _TimeoutProvider()
    adapter = ModelAdapter(ModelRegistryStub(), cfg)
    adapter._registry = SimpleNamespace(get_provider=lambda name: provider)

    sleeps = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    with pytest.raises(Exception, match="重试 3 次"):
        await adapter._call_with_retry("mock", [{"role": "user", "content": "hi"}])

    assert provider.calls == 4  # 1 + 3 次重试
    assert len(sleeps) == 3
    for attempt, s in enumerate(sleeps):
        assert 0 <= s <= min(30.0, 2.0 ** attempt)  # full jitter 上界 [0,2]/[0,4]/[0,8]


# ---------- SQLite 提交 BUSY 重试（§5.3：base=0.1s，最多 3 次） ----------


async def test_commit_retries_on_busy(db, monkeypatch):
    conn = await db.connect()
    # conn 是 RetryingConnection 代理；重试逻辑在代理内，故 patch 底层裸连接的 commit
    original_commit = conn._conn.commit
    attempts = {"n": 0}

    async def _flaky():
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise aiosqlite.OperationalError("database is locked")
        await original_commit()

    monkeypatch.setattr(conn._conn, "commit", _flaky)
    await db.commit()
    assert attempts["n"] == 3  # 两次 BUSY 后第三次成功


async def test_commit_non_lock_error_raises_immediately(db, monkeypatch):
    conn = await db.connect()
    attempts = {"n": 0}

    async def _boom():
        attempts["n"] += 1
        raise aiosqlite.OperationalError("disk I/O error")

    monkeypatch.setattr(conn._conn, "commit", _boom)
    with pytest.raises(aiosqlite.OperationalError):
        await db.commit()
    assert attempts["n"] == 1  # 非锁错误不重试
