"""把仓库 `.rsi/` 收成大屏用的一份快照。只读，不写日志、不建目录。"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ..memory.types import status_from_path

CAT_COLORS = {
    "conventions": "#21D3EE",
    "documentation": "#9A66F2",
    "gene-map": "#34D399",
    "episodes": "#FBBF24",
    "teaching-cases": "#F472B6",
    "patterns": "#60A5FA",
    "skills": "#A78BFA",
    "prohibitions": "#FB7185",
    "other": "#5E7196",
}
STATUS_COLORS = {
    "active": "#34D399",
    "pending_review": "#FBBF24",
    "archived": "#21D3EE",
}
_SHOWN_CATS = (
    "conventions",
    "documentation",
    "gene-map",
    "episodes",
    "teaching-cases",
    "patterns",
    "skills",
    "prohibitions",
)
_LOCATION_DIRS = {"pending", "archive"}
_ADOPTED = {"accepted", "applied", "referenced", "modified"}
_DAY = 10


def build_snapshot(rsi_dir: Path) -> dict[str, Any]:
    """扫描 memory YAML 与 logs/events*.jsonl。目录不存在时返回空快照。"""
    root = Path(rsi_dir)
    mem_cat: Counter[str] = Counter()
    mem_status: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    mem_index: dict[str, tuple[str, str]] = {}
    pending_items: list[dict[str, str]] = []
    mem_total = 0

    memory_root = root / "memory"
    if memory_root.is_dir():
        for path in memory_root.rglob("*.yaml"):
            if not _inside(root, path):
                continue
            doc = _load_yaml(path)
            if doc is None:
                continue
            rel = _rel(root, path)
            cat = _category(rel)
            status = status_from_path(rel)
            mem_total += 1
            mem_cat[cat] += 1
            mem_status[status] += 1
            domain = str(doc.get("domain") or "").strip()
            if domain:
                domains[domain] += 1
            doc_id = doc.get("id")
            if isinstance(doc_id, str) and doc_id:
                mem_index[doc_id] = (cat, domain or "unknown")
            if status == "pending_review":
                pending_items.append({
                    "title": str(doc.get("title") or "(无标题)"),
                    "type": str(doc.get("type") or cat),
                    "domain": domain or "未知",
                    "updated": _stamp(doc.get("updated_at") or doc.get("created_at")),
                })

    daily: dict[str, dict[str, int]] = {}
    ev_counter: Counter[str] = Counter()
    feedback: Counter[str] = Counter()
    recent: list[tuple[str, str, dict[str, Any]]] = []
    hit_cat: Counter[str] = Counter()
    hit_domain: Counter[str] = Counter()
    hit_miss = 0
    hit_min: int | None = None
    hit_max: int | None = None

    for event in _iter_event_logs(root):
        kind = str(event.get("kind") or "?")
        ev_counter[kind] += 1
        ts = event.get("ts")
        ts_text = ts if isinstance(ts, str) else ""
        day = ts_text[:_DAY] if _is_day(ts_text[:_DAY]) else ""
        if day:
            bucket = daily.setdefault(day, _empty_day())
            bucket["events"] += 1
        if kind == "recall":
            retrieved = event.get("retrieved")
            hits = len(retrieved) if isinstance(retrieved, list) else 0
            if day:
                daily[day]["recall"] += 1
                daily[day]["hits"] += hits
            hit_min = hits if hit_min is None else min(hit_min, hits)
            hit_max = hits if hit_max is None else max(hit_max, hits)
            if isinstance(retrieved, list):
                for mid in retrieved:
                    info = mem_index.get(mid) if isinstance(mid, str) else None
                    if info:
                        hit_cat[info[0]] += 1
                        hit_domain[info[1]] += 1
                    else:
                        hit_miss += 1
        elif kind == "feedback":
            if day:
                daily[day]["feedback"] += 1
                daily[day]["fb_total"] += 1
            action = str(event.get("action") or "?")
            feedback[action] += 1
            if day and action in _ADOPTED:
                daily[day]["fb_accepted"] += 1
        if ts_text:
            recent.append((ts_text, kind, event))

    series = _fill_series(daily)
    recall_events = sum(bucket["recall"] for bucket in daily.values())
    recall_hits = sum(bucket["hits"] for bucket in daily.values())
    fb_total = sum(feedback.values())
    fb_adopted = sum(feedback.get(name, 0) for name in _ADOPTED)
    active = mem_status.get("active", 0)

    return {
        "meta": {
            "repo": str(root.parent),
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "spanStart": series[0]["date"] if series else None,
            "spanEnd": series[-1]["date"] if series else None,
            "memTotal": mem_total,
            "eventTotal": sum(ev_counter.values()),
            "recallTotalHits": recall_hits,
            "healthPct": round(active / mem_total * 100, 1) if mem_total else 0,
            "pendingReview": mem_status.get("pending_review", 0),
        },
        "memory": {
            "categories": _category_view(mem_cat),
            "status": _status_view(mem_status),
            "total": mem_total,
        },
        "domains": [{"label": label, "value": value} for label, value in domains.most_common(8)],
        "recallStats": {
            "events": recall_events,
            "hits": recall_hits,
            "avgPerEvent": round(recall_hits / recall_events, 1) if recall_events else 0,
            "minPerEvent": hit_min or 0,
            "maxPerEvent": hit_max or 0,
            "byCategory": _hit_categories(hit_cat),
            "byDomain": [{"label": label, "value": value} for label, value in hit_domain.most_common(8)],
            "unmappedHits": hit_miss,
        },
        "pendingItems": sorted(pending_items, key=lambda item: item["updated"], reverse=True)[:20],
        "events": dict(ev_counter),
        "feedback": {
            "breakdown": dict(feedback),
            "total": fb_total,
            "adopted": fb_adopted,
            "adoptionPct": round(fb_adopted / fb_total * 100, 1) if fb_total else 0,
        },
        "series": series,
        "recent": _recent(recent),
    }


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _load_yaml(path: Path) -> dict[str, Any] | None:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _category(rel: str) -> str:
    parts = [part for part in rel.split("/") if part]
    if len(parts) < 2 or parts[0] != "memory":
        return "other"
    rest = parts[1:-1]
    if rest and rest[0] in _LOCATION_DIRS:
        rest = rest[1:]
    if not rest:
        return "other"
    name = rest[0]
    return name if name in CAT_COLORS else "other"


def _stamp(value: Any) -> str:
    text = str(value or "")
    return text[:_DAY] if _is_day(text[:_DAY]) else ""


def _is_day(text: str) -> bool:
    if len(text) != _DAY or text[4] != "-" or text[7] != "-":
        return False
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True


def _empty_day() -> dict[str, int]:
    return {
        "recall": 0,
        "hits": 0,
        "feedback": 0,
        "events": 0,
        "fb_accepted": 0,
        "fb_total": 0,
    }


def _iter_event_logs(root: Path):
    logs = root / "logs"
    if not logs.is_dir():
        return
    paths = []
    current = logs / "events.jsonl"
    if current.is_file():
        paths.append(current)
    paths.extend(sorted(p for p in logs.glob("events-*.jsonl") if p.is_file()))
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _fill_series(daily: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    if not daily:
        return []
    start = datetime.fromisoformat(min(daily))
    end = datetime.fromisoformat(max(daily))
    series = []
    cursor = start
    while cursor <= end:
        key = cursor.strftime("%Y-%m-%d")
        bucket = daily.get(key, _empty_day())
        series.append({
            "date": key,
            "recall": bucket["recall"],
            "hits": bucket["hits"],
            "feedback": bucket["feedback"],
            "events": bucket["events"],
            "fb_accepted": bucket["fb_accepted"],
            "fb_total": bucket["fb_total"],
        })
        cursor += timedelta(days=1)
    return series


def _category_view(counts: Counter[str]) -> list[dict[str, Any]]:
    shown = []
    other = 0
    for name, value in counts.items():
        if name in _SHOWN_CATS:
            shown.append({"label": name, "value": value, "color": CAT_COLORS[name]})
        else:
            other += value
    if other:
        shown.append({"label": "other 其他", "value": other, "color": CAT_COLORS["other"]})
    shown.sort(key=lambda item: -item["value"])
    return shown


def _status_view(counts: Counter[str]) -> list[dict[str, Any]]:
    rows = [
        {"label": name, "value": value, "color": STATUS_COLORS.get(name, "#5E7196")}
        for name, value in counts.items()
    ]
    rows.sort(key=lambda item: -item["value"])
    return rows


def _hit_categories(counts: Counter[str]) -> list[dict[str, Any]]:
    ranked = counts.most_common()
    if len(ranked) > 8:
        head, tail = ranked[:7], ranked[7:]
        extra = sum(value for _, value in tail)
    else:
        head, extra = ranked, 0
    rows = [
        {"label": name, "value": value, "color": CAT_COLORS.get(name, CAT_COLORS["other"])}
        for name, value in head
    ]
    if extra:
        rows.append({"label": "other 其他", "value": extra, "color": CAT_COLORS["other"]})
    return rows


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _recent(items: list[tuple[str, str, dict[str, Any]]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for ts, kind, event in sorted(items, key=lambda row: row[0], reverse=True):
        day = ts[5:10] if len(ts) >= 10 else ts
        if kind == "recall":
            task = str(event.get("task") or "")
            retrieved = event.get("retrieved")
            hits = len(retrieved) if isinstance(retrieved, list) else 0
            text = f"[{day}] recall: {_clip(task, 54)} · 命中 {hits} 条"
        elif kind == "feedback":
            action = str(event.get("action") or "")
            if action == "ignored":
                continue
            rating = event.get("rating")
            extra = f" · 评分 {rating}" if rating else ""
            text = f"[{day}] feedback: {action}{extra}"
        elif kind == "review":
            text = f"[{day}] review: {event.get('action') or event.get('decision') or ''} 一条记忆"
        elif kind == "learn":
            text = f"[{day}] learn: 新增/修订记忆 · {_clip(str(event.get('task') or ''), 46)}"
        else:
            continue
        out.append({"kind": kind, "text": text})
        if len(out) >= 9:
            break
    return out
