"""失败模式挖掘（Spec v3.0 §4.5，每周离线任务第 1 步）。

v3.0 负信号源为 recall 反馈：rejected、rating ≤ 2、ignored（召回内容被宿主无视），
按 intent × strategy（召回臂）分桶；仅处理样本数 ≥ 5 的桶（避免偶发噪声）。
rejected+comment 的评语汇总为禁止项提案的证据（模板化提案，零 LLM）。
只读 interaction_logs（append-only 事实日志，改进机制永不改写，§3.9 安全边界）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from ..data.sqlite import SQLiteClient

logger = logging.getLogger(__name__)

MIN_BUCKET_SAMPLES = 5      # §3.9：仅处理样本数 ≥ 5 的桶
_MAX_BUCKET_SAMPLES = 20    # 单桶采样上限（证据截断）
_MAX_COMMENTS = 5           # 单桶评语证据上限


@dataclass
class FailureBucket:
    intent: str
    strategy_name: str
    log_ids: List[str] = field(default_factory=list)
    signals: Dict[str, int] = field(default_factory=dict)  # 信号类型 → 计数
    examples: List[Dict[str, str]] = field(default_factory=list)  # 采样证据（脱敏输入摘录）
    comments: List[str] = field(default_factory=list)  # rejected 评语（禁止项提案证据）

    @property
    def count(self) -> int:
        return len(self.log_ids)


async def mine_failures(
    db: SQLiteClient, days: int = 7, min_samples: int = MIN_BUCKET_SAMPLES
) -> List[FailureBucket]:
    """近 N 天负信号按 intent × strategy 分桶，返回样本数 ≥ min_samples 的桶（按样本数降序）"""
    conn = await db.connect()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with conn.execute(
        """
        SELECT id, intent, strategy_name, feedback_action, feedback_rating, feedback_comment,
               raw_input
        FROM interaction_logs
        WHERE created_at >= ? AND status != 'pending' AND (
            feedback_action IN ('rejected', 'ignored')
            OR (feedback_rating IS NOT NULL AND feedback_rating <= 2)
        )
        ORDER BY created_at DESC
        """,
        (since,),
    ) as cur:
        rows = await cur.fetchall()

    buckets: Dict[tuple, FailureBucket] = {}
    for row in rows:
        key = (row["intent"] or "unknown", row["strategy_name"] or "default")
        bucket = buckets.setdefault(key, FailureBucket(intent=key[0], strategy_name=key[1]))
        bucket.log_ids.append(row["id"])
        if row["feedback_action"] == "rejected":
            bucket.signals["rejected"] = bucket.signals.get("rejected", 0) + 1
            if row["feedback_comment"] and len(bucket.comments) < _MAX_COMMENTS:
                bucket.comments.append(row["feedback_comment"])
        if row["feedback_action"] == "ignored":
            bucket.signals["ignored"] = bucket.signals.get("ignored", 0) + 1
        if row["feedback_rating"] is not None and row["feedback_rating"] <= 2:
            bucket.signals["low_rating"] = bucket.signals.get("low_rating", 0) + 1
        if len(bucket.examples) < 3:
            bucket.examples.append({"input_excerpt": (row["raw_input"] or "")[:200]})

    result = [b for b in buckets.values() if b.count >= min_samples]
    result.sort(key=lambda b: -b.count)
    for b in result:
        b.log_ids = b.log_ids[:_MAX_BUCKET_SAMPLES]
    logger.info("失败模式挖掘：%d 个有效桶（近 %d 天，≥%d 样本）", len(result), days, min_samples)
    return result
