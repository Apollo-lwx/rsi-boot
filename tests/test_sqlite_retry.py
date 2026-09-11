"""ISSUE-1：SQLITE_BUSY 重试收口到连接层（Spec §5.3）。

现状：仅 SQLiteClient.commit() 有重试，业务层 34 处裸 conn.commit()/execute 绕过。
目标：connect() 返回重试代理，execute/commit 的 BUSY 错误统一指数退避重试，
业务层零改动获得韧性。
"""

import aiosqlite
import pytest

from rsi_boot.data.sqlite import RetryingConnection


class _OkCursor:
    rowcount = 1

    def __await__(self):
        async def _():
            return self
        return _().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetchone(self):
        return {"n": 1}


class _FailCursor:
    """await 时抛锁错误（模拟 aiosqlite：错误在 worker 线程执行时才浮现）"""

    def __await__(self):
        async def _():
            raise aiosqlite.OperationalError("database is locked")
        return _().__await__()


class FakeConn:
    """execute 前 fail_times 次在 await 时抛 BUSY，之后正常；commit 同理"""

    def __init__(self, execute_failures=0, commit_failures=0):
        self.execute_failures = execute_failures
        self.commit_failures = commit_failures
        self.execute_attempts = 0
        self.commit_attempts = 0

    def execute(self, sql, params=()):
        self.execute_attempts += 1
        if self.execute_failures > 0:
            self.execute_failures -= 1
            return _FailCursor()
        return _OkCursor()

    async def commit(self):
        self.commit_attempts += 1
        if self.commit_failures > 0:
            self.commit_failures -= 1
            raise aiosqlite.OperationalError("database is locked")

    async def rollback(self):
        return None


def _wrap(fake, **kw):
    kw.setdefault("base_s", 0.001)  # 测试加速：退避基数 1ms
    return RetryingConnection(fake, **kw)


async def test_execute_retries_on_busy_then_succeeds():
    fake = FakeConn(execute_failures=2)
    cur = await _wrap(fake).execute("INSERT INTO t VALUES (1)")
    assert cur.rowcount == 1
    assert fake.execute_attempts == 3  # 2 次失败 + 第 3 次成功


async def test_execute_async_context_manager_retries():
    fake = FakeConn(execute_failures=1)
    async with _wrap(fake).execute("SELECT 1") as cur:
        assert (await cur.fetchone())["n"] == 1
    assert fake.execute_attempts == 2


async def test_execute_non_lock_error_not_retried():
    class SyntaxConn(FakeConn):
        def execute(self, sql, params=()):
            self.execute_attempts += 1
            class _Bad:
                def __await__(self):
                    async def _():
                        raise aiosqlite.OperationalError('near "SELCT": syntax error')
                    return _().__await__()
            return _Bad()

    fake = SyntaxConn()
    with pytest.raises(aiosqlite.OperationalError, match="syntax error"):
        await _wrap(fake).execute("SELCT 1")
    assert fake.execute_attempts == 1  # 非锁错误不重试


async def test_commit_retries_on_busy():
    fake = FakeConn(commit_failures=2)
    await _wrap(fake).commit()
    assert fake.commit_attempts == 3


async def test_retry_exhausted_raises():
    fake = FakeConn(execute_failures=99)
    with pytest.raises(aiosqlite.OperationalError, match="locked"):
        await _wrap(fake, max_retries=3).execute("INSERT INTO t VALUES (1)")
    assert fake.execute_attempts == 4  # 1 + 3 次重试


async def test_rollback_and_attrs_delegate():
    fake = FakeConn()
    conn = _wrap(fake)
    await conn.rollback()  # 不抛异常即委托成功
    assert conn.execute_attempts == 0


# ---------- 真实库集成：并发写锁下操作最终成功 ----------


async def test_real_db_write_survives_transient_lock(db, monkeypatch, tmp_path):
    """另一连接持有写锁 0.3s 期间，客户端写入经重试最终成功（不抛 database is locked）"""
    import sqlite3
    import threading

    import rsi_boot.data.sqlite as sqlite_mod

    monkeypatch.setattr(sqlite_mod, "_BUSY_RETRY_BASE_S", 0.05)
    locker = sqlite3.connect(str(tmp_path / "test.db"), timeout=0, check_same_thread=False)
    locker.execute("BEGIN IMMEDIATE")
    locker.execute("INSERT INTO interaction_logs (id, request_id, user_id, raw_input, status,"
                   " latency_ms, feedback_token, created_at) VALUES ('lock-row', 'req-lock', 'u',"
                   " 'q', 'success', 1, 'tok-lock', '2026-01-01T00:00:00+00:00')")

    def release_soon():
        import time
        time.sleep(0.3)
        locker.commit()
        locker.close()

    threading.Thread(target=release_soon, daemon=True).start()
    # busy_timeout 5000ms 内锁释放，写入应成功而非抛锁错误
    await db.execute(
        "INSERT INTO interaction_logs (id, request_id, user_id, raw_input, status,"
        " latency_ms, feedback_token, created_at) VALUES ('w1', 'req-w1', 'u', 'q', 'success',"
        " 1, 'tok-w1', '2026-01-01T00:00:00+00:00')"
    )
    await db.commit()
