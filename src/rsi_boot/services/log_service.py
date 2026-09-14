"""交互日志服务（§4.1 两段式写入）。

模型调用前 INSERT status='pending'（含 request_id 与 feedback_token），
响应完成后 UPDATE 终态——即使模型调用后进程崩溃，token 也已持久化可关联；
遗留 pending 记录由离线对账任务补齐（§4.3，Phase 3）。

interaction_logs 为 append-only 事实日志（§3.9 安全边界）：
仅允许 pending → 终态 的状态推进与反馈字段填写，禁止改写已终态记录。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional

from ..core.masking import mask_text
from ..core.models import RSIRequest
from ..memory.logstore import append_event, iter_events
from ..memory.store import MemoryStore

_DOC_ID = re.compile(r"^[0-9a-f]{32}$")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LogService:
    def __init__(self, store: MemoryStore):
        self._store = store

    async def insert_pending(self, request: RSIRequest, feedback_token: str) -> str:
        """模型调用前落 pending 日志，返回日志 id。"""
        log_id = uuid.uuid4().hex
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
        log_id 为空时跳过"""
        if not log_id:
            return
        retrieved = [
            x for x in (retrieved_tags or [])
            if isinstance(x, str) and _DOC_ID.fullmatch(x)
        ]
        pending = None
        for event in iter_events(self._store.rsi_dir):
            if event.get("id") == log_id:
                pending = event
                if event.get("status") == "pending":
                    break
        token = (pending or {}).get("token")
        kind = "recall"
        if intent and (intent == "recall" or str(intent).startswith("recall")):
            kind = intent
        event: dict[str, Any] = {
            "id": log_id,
            "ts": _utc_iso(),
            "kind": kind,
            "status": status,
            "retrieved": retrieved,
            "arm": strategy_name,
            "latency_ms": latency_ms,
            "excerpt": mask_text(response_excerpt[:4000]) if response_excerpt else None,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": int(prompt_tokens or 0) + int(completion_tokens or 0),
        }
        if quality_score is not None:
            event["quality_score"] = quality_score
        if intent:
            event["intent"] = intent
        if model_name:
            event["model_name"] = model_name
        if cost_usd is not None:
            event["cost_usd"] = float(cost_usd)
        if token:
            event["token"] = token
        if pending:
            for key in ("project_id", "user_id", "task"):
                if pending.get(key) is not None:
                    event[key] = pending[key]
        append_event(self._store.rsi_dir, event)

    async def apply_feedback(
        self, feedback_token: str, action: str, rating: Optional[int], comment: Optional[str] = None
    ) -> bool:
        """按 token 回查日志并填写反馈字段（§4.2）。comment 脱敏后落库（≤2048 字符），
        是 rejected+comment → 禁止项提取（§4.4）与失败挖掘证据的输入。
        返回 False 表示 token 不存在（调用方先做 HMAC 校验）"""
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

    async def refresh_quality_score(self, feedback_token: str, quality_score: float) -> bool:
        """差评必查回写（§3.5）：rating ≤ 2 触发 accuracy 补查后，以含 accuracy 的
        完整权重分刷新 quality_score。属反馈字段填写的延伸（派生数据，非事实改写）"""
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

    async def get_request_id_by_token(self, feedback_token: str) -> Optional[str]:
        for event in iter_events(self._store.rsi_dir):
            if event.get("token") == feedback_token:
                return event.get("request_id") or event.get("id")
        return None

    async def get_token_context(self, feedback_token: str) -> Optional[tuple[str, str]]:
        """按 token 取 (request_id, user_id)，供 HMAC 校验"""
        for event in iter_events(self._store.rsi_dir):
            if event.get("token") == feedback_token:
                return (event.get("request_id") or event.get("id") or "", event.get("user_id") or "local")
        return None

    async def daily_token_usage(self) -> int:
        """当日 00:00 UTC 起累计 token（§6.2 预算检查）。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
        total = 0
        for event in iter_events(self._store.rsi_dir):
            ts = str(event.get("ts") or event.get("created_at") or "")
            if ts >= today:
                total += int(event.get("total_tokens") or 0)
        return total
