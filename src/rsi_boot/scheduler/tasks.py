"""离线任务（§4.3 / §8.3 数据生命周期）。

归档：超过保留期（默认 90 天）的交互日志导出为 `.rsi/archive/YYYYMM.jsonl`
后从库中删除。导出先于删除，导出失败则不删除（数据安全优先）。
策略调优（§3.3，每周）：alpha/beta 向先验回归（×0.9 防历史锁定）+
淘汰曝光 ≥100 且采纳率 <20% 的策略（is_active=0，人工复核后删除）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from ..data.sqlite import SQLiteClient

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 90

# §3.3 策略调优参数
_DECAY_FACTOR = 0.9           # alpha/beta 向先验回归系数
_ELIMINATE_MIN_EXPOSURE = 100  # 淘汰最小曝光量
_ELIMINATE_MAX_ACCEPT = 0.2    # 采纳率阈值


async def archive_old_logs(
    db: SQLiteClient,
    archive_dir: Path,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> int:
    """归档并删除过期日志，返回归档条数"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    conn = await db.connect()
    async with conn.execute(
        "SELECT * FROM interaction_logs WHERE created_at < ? AND status != 'pending' ORDER BY created_at",
        (cutoff,),
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return 0

    # 按月份分组写入 YYYYMM.jsonl（追加模式，同月多次归档不覆盖）
    by_month: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        month = row["created_at"][:7].replace("-", "")
        by_month.setdefault(month, []).append(dict(row))

    archive_dir.mkdir(parents=True, exist_ok=True)
    for month, month_rows in by_month.items():
        path = archive_dir / f"{month}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for r in month_rows:
                fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    ids = [row["id"] for row in rows]
    await conn.execute(
        f"DELETE FROM interaction_logs WHERE id IN ({','.join('?' * len(ids))})",
        tuple(ids),
    )
    await conn.commit()
    logger.info("已归档 %d 条日志到 %s", len(rows), archive_dir)
    return len(rows)


async def tune_strategies(db: SQLiteClient) -> Dict[str, int]:
    """§3.3 每周策略调优：衰减 + 淘汰。返回 {decayed, eliminated} 计数"""
    conn = await db.connect()
    now = datetime.now(timezone.utc).isoformat()

    # 衰减：alpha = 1 + (alpha-1)×0.9，beta 同理（防历史数据锁定导致策略固化）
    cur = await conn.execute(
        "UPDATE strategy_configs SET alpha = 1 + (alpha - 1) * ?, beta = 1 + (beta - 1) * ?, updated_at = ? "
        "WHERE is_active = 1 AND (alpha != 1.0 OR beta != 1.0)",
        (_DECAY_FACTOR, _DECAY_FACTOR, now),
    )
    decayed = cur.rowcount

    # 淘汰：曝光 ≥100 且后验采纳率（alpha/(alpha+beta)）< 20%
    cur = await conn.execute(
        "UPDATE strategy_configs SET is_active = 0, updated_at = ? "
        "WHERE is_active = 1 AND exposure_count >= ? AND alpha / (alpha + beta) < ?",
        (now, _ELIMINATE_MIN_EXPOSURE, _ELIMINATE_MAX_ACCEPT),
    )
    eliminated = cur.rowcount
    await conn.commit()
    if eliminated:
        logger.warning("策略淘汰：%d 条标记 is_active=0（人工复核后删除）", eliminated)
    return {"decayed": decayed, "eliminated": eliminated}
