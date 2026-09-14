"""交互日志服务（§4.1 两段式写入）。

模型调用前 INSERT status='pending'（含 request_id 与 feedback_token），
响应完成后 UPDATE 终态——即使模型调用后进程崩溃，token 也已持久化可关联；
遗留 pending 记录由离线对账任务补齐（§4.3，Phase 3）。

interaction_logs 为 append-only 事实日志（§3.9 安全边界）：
仅允许 pending → 终态 的状态推进与反馈字段填写，禁止改写已终态记录。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional

from ..core.exceptions import CircuitOpenError
from ..core.masking import mask_text
from ..core.models import RSIRequest
from ..data.sqlite import SQLiteClient, is_operational_error
from ..memory.logstore import append_event, iter_events
from ..memory.store import MemoryStore

logger = logging.getLogger(__name__)

_DOC_ID = re.compile(r"^[0-9a-f]{32}$")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LogService:
    def __init__(self, db: Optional[SQLiteClient] = None, store: MemoryStore | None = None):
        if store is None and db is None:
            raise TypeError("db is required when store is omitted")
        self._db = db
        self._store = store

    async def insert_pending(self, request: RSIRequest, feedback_token: str) -> str:
        """模型调用前落 pending 日志，返回日志 id。

        §5.4 Level 2 降级：sqlite 熔断 OPEN 或写失败时跳过持久化（响应照常返回），
        返回 ""——调用方据此跳过 finalize；反馈路径（apply_feedback）不做 fail-soft，
        显式反馈写失败须如实报错（§4.2 同步落库语义）。
        """
        log_id = uuid.uuid4().hex
        if self._store is not None:
            append_event(self._store.rsi_dir, {
                "id": log_id,
                "ts": _utc_iso(),
                "kind": "recall",
                "task": request.raw_input,
                "token": feedback_token,
                "status": "pending",
                "project_id": request.project_id,
                "user_id": request.user_id,
                "retrieved": [],
            })
            return log_id
        try:
            self._db.breaker.allow_request()
            conn = await self._db.connect()
            await conn.execute(
                """
                INSERT INTO interaction_logs
                    (id, request_id, user_id, project_id, session_id, raw_input, context,
                     latency_ms, status, feedback_token, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?, ?)
                """,
                (
                    log_id,
                    str(request.request_id),
                    request.user_id,
                    request.project_id,
                    request.session_id,
                    mask_text(request.raw_input),
                    mask_text(request.context.model_dump_json()),
                    feedback_token,
                    _utc_iso(),
                ),
            )
            await self._db.commit()
            self._db.breaker.on_success()
            return log_id
        except CircuitOpenError:
            logger.error("sqlite 熔断 OPEN，跳过日志持久化（本次请求仅内存响应）")
            return ""
        except Exception as exc:
            self._db.breaker.on_failure(countable=is_operational_error(exc))
            logger.error("日志 pending 写入失败（跳过持久化）: %s", exc)
            return ""

    async def finalize(
        self,
        log_id: str,
        *,
        status: str,
        intent: Optional[str] = None,
        intent_confidence: Optional[float] = None,
        strategy_name: Optional[str] = None,
        model_name: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: Optional[Decimal] = None,
        latency_ms: int = 0,
        quality_score: Optional[float] = None,
        response_excerpt: Optional[str] = None,
        retrieved_tags: Optional[List[str]] = None,
    ) -> None:
        """响应完成后推进终态（success/error）。仅允许从 pending 推进。
        response_excerpt 为脱敏后响应摘录（≤4000 字符），供 §4.3 知识提取候选使用；
        retrieved_tags 为检索命中条目的 domain/tags 快照（§4.2 反馈兴趣加权依据）。
        log_id 为空（pending 写入已降级）或写失败时跳过（§5.4 Level 2）"""
        if not log_id:
            return
        if self._store is not None:
            retrieved = [
                x for x in (retrieved_tags or [])
                if isinstance(x, str) and _DOC_ID.fullmatch(x)
            ]
            append_event(self._store.rsi_dir, {
                "id": log_id,
                "ts": _utc_iso(),
                "kind": intent or "recall",
                "status": status,
                "retrieved": retrieved,
                "arm": strategy_name,
                "latency_ms": latency_ms,
                "excerpt": mask_text(response_excerpt[:4000]) if response_excerpt else None,
            })
            return
        try:
            self._db.breaker.allow_request()
        except CircuitOpenError:
            logger.error("sqlite 熔断 OPEN，跳过终态回写")
            return
        try:
            conn = await self._db.connect()
            await conn.execute(
                """
                UPDATE interaction_logs
                SET status = ?, intent = ?, intent_confidence = ?, strategy_name = ?, model_name = ?,
                    prompt_tokens = ?, completion_tokens = ?, total_tokens = ?, cost_usd = ?,
                    latency_ms = ?, quality_score = ?, response_excerpt = ?, retrieved_tags = ?,
                    completed_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    status,
                    intent,
                    intent_confidence,
                    strategy_name,
                    model_name,
                    prompt_tokens,
                    completion_tokens,
                    prompt_tokens + completion_tokens,
                    float(cost_usd) if cost_usd is not None else None,
                    latency_ms,
                    quality_score,
                    mask_text(response_excerpt[:4000]) if response_excerpt else None,
                    json.dumps(retrieved_tags, ensure_ascii=False) if retrieved_tags else None,
                    _utc_iso(),
                    log_id,
                ),
            )
            await self._db.commit()
            self._db.breaker.on_success()
        except Exception as exc:
            self._db.breaker.on_failure(countable=is_operational_error(exc))
            logger.error("日志终态回写失败（跳过持久化）: %s", exc)

    async def apply_feedback(
        self, feedback_token: str, action: str, rating: Optional[int], comment: Optional[str] = None
    ) -> bool:
        """按 token 回查日志并填写反馈字段（§4.2）。comment 脱敏后落库（≤2048 字符），
        是 rejected+comment → 禁止项提取（§4.4）与失败挖掘证据的输入。
        返回 False 表示 token 不存在（调用方先做 HMAC 校验）"""
        if self._store is not None:
            found = False
            for event in iter_events(self._store.rsi_dir):
                if event.get("token") == feedback_token:
                    found = True
                    break
            if not found:
                return False
            append_event(self._store.rsi_dir, {
                "id": uuid.uuid4().hex,
                "ts": _utc_iso(),
                "kind": "feedback",
                "token": feedback_token,
                "action": action,
                "rating": rating,
                "comment": mask_text(comment[:2048]) if comment else None,
            })
            return True
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT id FROM interaction_logs WHERE feedback_token = ?", (feedback_token,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False
        await conn.execute(
            "UPDATE interaction_logs SET feedback_action = ?, feedback_rating = ?,"
            " feedback_comment = ? WHERE id = ?",
            (action, rating, mask_text(comment[:2048]) if comment else None, row["id"]),
        )
        await conn.commit()
        return True

    async def refresh_quality_score(self, feedback_token: str, quality_score: float) -> bool:
        """差评必查回写（§3.5）：rating ≤ 2 触发 accuracy 补查后，以含 accuracy 的
        完整权重分刷新 quality_score。属反馈字段填写的延伸（派生数据，非事实改写）"""
        if self._store is not None:
            found = any(e.get("token") == feedback_token for e in iter_events(self._store.rsi_dir))
            if not found:
                return False
            append_event(self._store.rsi_dir, {
                "id": uuid.uuid4().hex,
                "ts": _utc_iso(),
                "kind": "audit",
                "token": feedback_token,
                "quality_score": quality_score,
            })
            return True
        conn = await self._db.connect()
        cur = await conn.execute(
            "UPDATE interaction_logs SET quality_score = ? WHERE feedback_token = ?",
            (quality_score, feedback_token),
        )
        await conn.commit()
        return cur.rowcount > 0

    async def get_request_id_by_token(self, feedback_token: str) -> Optional[str]:
        if self._store is not None:
            for event in iter_events(self._store.rsi_dir):
                if event.get("token") == feedback_token:
                    return event.get("request_id") or event.get("id")
            return None
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT request_id FROM interaction_logs WHERE feedback_token = ?", (feedback_token,)
        ) as cur:
            row = await cur.fetchone()
        return row["request_id"] if row else None

    async def get_token_context(self, feedback_token: str) -> Optional[tuple[str, str]]:
        """按 token 取 (request_id, user_id)，供 HMAC 校验"""
        if self._store is not None:
            for event in iter_events(self._store.rsi_dir):
                if event.get("token") == feedback_token:
                    return (event.get("request_id") or event.get("id") or "", event.get("user_id") or "local")
            return None
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT request_id, user_id FROM interaction_logs WHERE feedback_token = ?",
            (feedback_token,),
        ) as cur:
            row = await cur.fetchone()
        return (row["request_id"], row["user_id"]) if row else None

    async def daily_token_usage(self) -> int:
        """当日 00:00 UTC 起累计 token（§6.2 预算检查）。
        sqlite 不可用时返回 0（Level 2 下预算检查失效，记 error 日志）"""
        if self._store is not None:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
            total = 0
            for event in iter_events(self._store.rsi_dir):
                ts = str(event.get("ts") or event.get("created_at") or "")
                if ts >= today:
                    total += int(event.get("total_tokens") or 0)
            return total
        try:
            conn = await self._db.connect()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
            async with conn.execute(
                "SELECT COALESCE(SUM(total_tokens), 0) FROM interaction_logs WHERE created_at >= ?",
                (today,),
            ) as cur:
                row = await cur.fetchone()
            return int(row[0]) if row else 0
        except Exception as exc:
            logger.error("用量查询失败（本次预算检查跳过）: %s", exc)
            return 0
