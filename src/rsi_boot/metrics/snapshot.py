"""机会式日快照：任意 recall/impact 运行时顺带落一条当日聚合到 .rsi/state/impacts.jsonl。

零 Key stdio 无常驻进程，靠「顺手续写」攒长期曲线：
- 每天最多一条（同日已存在则跳过，避免热路径放大 IO）；
- append-only（与 events.jsonl 同风格），读取时按日期折叠取当日最新；
- 字段全是标量（召回数、触达字符、全库字符、采纳率、合规率、footprint），体量恒定小。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_SNAPSHOT_NAME = "impacts.jsonl"


def snapshot_path(rsi_dir: Path) -> Path:
    return Path(rsi_dir) / "state" / _SNAPSHOT_NAME


def _today(rsi_dir: Path) -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def read_snapshots(rsi_dir: Path) -> List[Dict[str, Any]]:
    """按日期升序返回折叠后的日快照（每日取最后写入的一条）。坏行跳过。"""
    path = snapshot_path(rsi_dir)
    if not path.is_file():
        return []
    by_date: Dict[str, Dict[str, Any]] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("date"):
            by_date[str(obj["date"])] = obj
    return [by_date[d] for d in sorted(by_date)]


def latest_snapshot(rsi_dir: Path) -> Optional[Dict[str, Any]]:
    series = read_snapshots(rsi_dir)
    return series[-1] if series else None


def has_today(rsi_dir: Path) -> bool:
    path = snapshot_path(rsi_dir)
    if not path.is_file():
        return False
    today = _today(rsi_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    # 轻量前缀判定，命中则今天已落过，跳过追加
    return f'"date": "{today}"' in text or f'"date":"{today}"' in text


def append_snapshot(rsi_dir: Path, record: Dict[str, Any], *, throttle_daily: bool = True) -> bool:
    """追加一条日快照。throttle_daily=True 时同日只落第一条（返回 False 表示跳过）。

    永不抛异常打断调用方（recall 热路径）：写失败静默返回 False。
    """
    try:
        if throttle_daily and has_today(rsi_dir):
            return False
        dest = snapshot_path(rsi_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        row = {"date": _today(rsi_dir), **record}
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
        return True
    except OSError:
        return False
