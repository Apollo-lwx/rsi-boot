"""SQLite 客户端（aiosqlite，WAL 模式，§2.2）。

单文件库：项目根目录 `.rsi/rsi.db`。所有写操作经本模块的连接句柄，
读不阻塞写（WAL）。启动时执行 PRAGMA integrity_check 自检（§5 风险表）。

韧性（§5.2/§5.3）：持有 sqlite 熔断器（阈值 3，恢复 30s），由服务层在
读写失败时上报；commit 对 SQLITE_BUSY 做 full-jitter 重试（base=0.1s，最多 3 次）。
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Optional

import aiosqlite

from ..common.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

_BUSY_RETRY_BASE_S = 0.1  # §5.3：SQLite 写入指数退避 base=0.1s + full jitter
_BUSY_MAX_RETRIES = 3


def is_operational_error(exc: BaseException) -> bool:
    """熔断计数口径（§5.1）：仅连接/IO/锁等运行态错误计入；约束冲突等业务错误不计"""
    return isinstance(exc, aiosqlite.OperationalError)


def _is_busy(exc: BaseException) -> bool:
    return isinstance(exc, aiosqlite.OperationalError) and "lock" in str(exc).lower()


async def _with_busy_retry(fn, *, max_retries: int, base_s: float):
    """§5.3：SQLITE_BUSY 指数退避 full jitter（base=0.1s）最多 3 次；其余错误直接抛"""
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except aiosqlite.OperationalError as exc:
            if not _is_busy(exc) or attempt >= max_retries:
                raise
            await asyncio.sleep(random.uniform(0, base_s * 2 ** attempt))


class _ExecuteRequest:
    """conn.execute(...) 的重试包装：同时支持 await 与 async with 两种消费形态"""

    def __init__(self, conn: aiosqlite.Connection, sql: str, params: tuple,
                 max_retries: int, base_s: float):
        self._conn = conn
        self._sql = sql
        self._params = params
        self._max_retries = max_retries
        self._base_s = base_s
        self._cursor: Optional[aiosqlite.Cursor] = None

    async def _run(self) -> aiosqlite.Cursor:
        return await _with_busy_retry(
            lambda: self._conn.execute(self._sql, self._params),
            max_retries=self._max_retries, base_s=self._base_s,
        )

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self) -> aiosqlite.Cursor:
        self._cursor = await self._run()
        return await self._cursor.__aenter__()

    async def __aexit__(self, *exc):
        return await self._cursor.__aexit__(*exc)


class RetryingConnection:
    """aiosqlite 连接代理：execute/commit 的 BUSY 错误统一退避重试（§5.3 收口点）。

    业务层经 SQLiteClient.connect() 拿到的即本代理——所有裸 conn.execute/commit
    调用自动获得重试，无需逐点改造。其余属性/方法（rollback 等）原样委托。
    """

    def __init__(self, conn: aiosqlite.Connection, *,
                 max_retries: Optional[int] = None, base_s: Optional[float] = None):
        self._conn = conn
        # None → 构造时读模块常量（测试可 monkeypatch 加速退避）
        self._max_retries = _BUSY_MAX_RETRIES if max_retries is None else max_retries
        self._base_s = _BUSY_RETRY_BASE_S if base_s is None else base_s

    def execute(self, sql: str, params: tuple = ()) -> _ExecuteRequest:
        return _ExecuteRequest(self._conn, sql, params, self._max_retries, self._base_s)

    async def commit(self) -> None:
        await _with_busy_retry(self._conn.commit,
                               max_retries=self._max_retries, base_s=self._base_s)

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


class SQLiteClient:
    """aiosqlite 连接封装：懒连接 + WAL + 行工厂；connect() 返回重试代理"""

    def __init__(self, db_path: Path | str, busy_timeout_ms: int = 5000):
        self.db_path = Path(db_path)
        self._busy_timeout_ms = busy_timeout_ms
        self._conn: Optional[RetryingConnection] = None
        # §5.2：sqlite 依赖独立熔断器（阈值 3 / 恢复 30s），服务层共享此实例
        self.breaker = CircuitBreaker(f"sqlite:{self.db_path.name}", threshold=3, recovery_timeout_s=30.0)

    async def connect(self) -> RetryingConnection:
        if self._conn is not None:
            return self._conn
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(self.db_path))
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        await conn.commit()
        self._conn = RetryingConnection(conn)
        return self._conn

    async def integrity_check(self) -> bool:
        """启动自检；损坏时调用方负责备份重建（§5 风险表）"""
        conn = await self.connect()
        async with conn.execute("PRAGMA integrity_check") as cur:
            row = await cur.fetchone()
        ok = bool(row) and row[0] == "ok"
        if not ok:
            logger.error("SQLite integrity_check 失败: %s", row[0] if row else "no row")
        return ok

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        conn = await self.connect()
        return await conn.execute(sql, params)

    async def commit(self) -> None:
        """§5.3 重试由连接代理承载，此处仅委托"""
        if self._conn is None:
            return
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "SQLiteClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
