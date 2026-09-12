"""对话内抉择队列：进程内 sticky / suppress / close，并收集召回决策卡。"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from ..cli.knowledge_accept import load_latest_run_id
from ..data.sqlite import SQLiteClient

_EXTRACT_SIGNALS = frozenset({"signal:conversation", "signal:rules"})
_KIND_PRIORITY = {
    "daily_conflict": 0,
    "bootstrap_conflict": 1,
    "bootstrap_extract": 2,
}
_EXCERPT_MAX = 200
_DEFAULT_SUPPRESS_S = 86400.0


@dataclass
class DecisionCard:
    id: str
    kind: str  # daily_conflict | bootstrap_extract | bootstrap_conflict
    prompt: str
    options: list[dict]
    recommended: str
    recommended_reason: str
    sides: list[dict]
    impact: dict
    more_waiting: int = 0

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


class DecisionQueue:
    """进程内抉择状态。不落库；热加载不得重建。"""

    def __init__(self) -> None:
        self._sticky: Optional[str] = None
        self._suppressed: dict[str, float] = {}
        self._closed: set[str] = set()

    def pick(self, cards: list[DecisionCard]) -> DecisionCard | None:
        """sticky 未关闭的 id 优先；被 suppress 的跳过。"""
        eligible = self._eligible(cards)
        if not eligible:
            return None
        if self._sticky is not None:
            for card in eligible:
                if card.id == self._sticky:
                    return card
        eligible.sort(key=lambda c: _KIND_PRIORITY.get(c.kind, 99))
        return eligible[0]

    def mark_presented(self, decision_id: str) -> None:
        self._sticky = decision_id

    def suppress(self, decision_id: str, seconds: float = _DEFAULT_SUPPRESS_S) -> None:
        self._suppressed[decision_id] = time.monotonic() + seconds
        if self._sticky == decision_id:
            self._sticky = None

    def close(self, decision_id: str) -> None:
        self._closed.add(decision_id)
        self._suppressed.pop(decision_id, None)
        if self._sticky == decision_id:
            self._sticky = None

    def remaining_after(self, cards: list[DecisionCard], picked: DecisionCard) -> int:
        return sum(1 for c in self._eligible(cards) if c.id != picked.id)

    def _eligible(self, cards: list[DecisionCard]) -> list[DecisionCard]:
        now = time.monotonic()
        out: list[DecisionCard] = []
        for card in cards:
            if card.id in self._closed:
                continue
            expiry = self._suppressed.get(card.id)
            if expiry is not None and expiry > now:
                continue
            out.append(card)
        return out


def _parse_tags(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def _has_bootstrap_run_tag(tags: list[str]) -> bool:
    return any(t.startswith("bootstrap_run_id:") or t == "bootstrap_run_id" for t in tags)


def _excerpt(text: str) -> str:
    return (text or "")[:_EXCERPT_MAX]


def _conflict_options() -> list[dict]:
    return [
        {"id": "keep_item", "label": "用新的（对话里刚确认的）", "resolution": "keep_item"},
        {"id": "keep_peer", "label": "维持原来的", "resolution": "keep_peer"},
        {"id": "coexist", "label": "两版都留，以后按场景再看", "resolution": "coexist"},
    ]


def _recommended_of(note: str | None) -> str:
    raw = note or ""
    if raw.startswith("recommended:"):
        token = raw.split(":", 1)[1].strip().split()[0]
        if token:
            return token
    return "keep_item"


async def _peer_row(conn: Any, project_id: str, source: str) -> Optional[dict[str, Any]]:
    if not source:
        return None
    norm = source.replace("\\", "/")
    async with conn.execute(
        "SELECT id, title, content, source_url, status, tags FROM knowledge_items "
        "WHERE project_id = ?",
        (project_id,),
    ) as cur:
        rows = await cur.fetchall()
    for row in rows:
        if (row["source_url"] or "").replace("\\", "/") == norm:
            return dict(row)
    return None


def _conflict_card(
    *,
    conflict_id: str,
    kind: str,
    ctype: str,
    item: dict[str, Any],
    peer: Optional[dict[str, Any]],
    peer_source: str,
    excerpt: str,
    recommended: str,
    recommended_reason: str,
) -> DecisionCard:
    item_title = item.get("title") or "新侧"
    peer_title = (peer or {}).get("title") or peer_source or "对侧"
    item_src = item.get("source_url") or ""
    prompt = f"出现「{item_title}」，和已有的「{peer_title}」不一致。你要哪边？"
    return DecisionCard(
        id=conflict_id,
        kind=kind,
        prompt=prompt,
        options=_conflict_options(),
        recommended=recommended if kind != "daily_conflict" else "keep_item",
        recommended_reason=recommended_reason or excerpt,
        sides=[
            {
                "role": "new",
                "title": item_title,
                "excerpt": _excerpt(item.get("content") or excerpt),
                "source": item_src,
            },
            {
                "role": "old",
                "title": peer_title,
                "excerpt": _excerpt((peer or {}).get("content") or excerpt),
                "source": peer_source,
            },
        ],
        impact={
            "recall": "之后相关任务会按你选的那条注入",
            "inject": "规则文件 rsi-*.mdc / AGENTS.md 会改哪一侧",
            "code_hint": peer_source if ctype == "doc_code" else "",
        },
    )


def _extract_card(run_id: str, pending_n: int) -> DecisionCard:
    return DecisionCard(
        id=f"extract:{run_id}",
        kind="bootstrap_extract",
        prompt="本轮从对话/规则抽出的条目尚未确认。全部生效、全部丢掉，还是先跳过？",
        options=[
            {"id": "approve", "label": "本轮抽取全部生效"},
            {"id": "reject", "label": "本轮抽取全部丢掉"},
            {
                "id": "skip",
                "label": "先跳过（本会话抑制，不调 review）",
                "note": "skip means suppress only; do not call review",
            },
        ],
        recommended="approve",
        recommended_reason=f"本轮还有 {pending_n} 条抽取待确认，确认后才会进入召回",
        sides=[],
        impact={
            "recall": "批准后这些抽取进入召回；拒绝则丢弃",
            "inject": "生效后可能写入 rsi-*.mdc / AGENTS.md",
            "code_hint": "",
        },
    )


async def collect_decision_cards(
    db: SQLiteClient,
    project_id: str,
    *,
    project_root: Optional[Path] = None,
) -> list[DecisionCard]:
    """收集当前可问的抉择卡（未 pick、未算 more_waiting）。"""
    conn = await db.connect()
    async with conn.execute(
        "SELECT c.id, c.item_id, c.user_rule_path, c.user_rule_excerpt, c.conflict_type,"
        " c.resolution_note, k.title AS item_title, k.content AS item_content,"
        " k.source_url AS item_source, k.tags AS item_tags, k.status AS item_status"
        " FROM rule_conflicts c LEFT JOIN knowledge_items k ON k.id = c.item_id"
        " WHERE c.project_id = ? AND c.status = 'open'"
        " AND c.conflict_type IN ('incoherent', 'version', 'doc_code')",
        (project_id,),
    ) as cur:
        rows = await cur.fetchall()

    cards: list[DecisionCard] = []
    for row in rows:
        item = {
            "id": row["item_id"],
            "title": row["item_title"] or "",
            "content": row["item_content"] or "",
            "source_url": row["item_source"] or "",
            "status": row["item_status"] or "",
            "tags": row["item_tags"],
        }
        tags = _parse_tags(item["tags"])
        source = item["source_url"]
        if source == "auto-extract" or not _has_bootstrap_run_tag(tags):
            kind = "daily_conflict"
        else:
            kind = "bootstrap_conflict"
        peer_source = (row["user_rule_path"] or "").replace("\\", "/")
        peer = await _peer_row(conn, project_id, peer_source)
        cards.append(_conflict_card(
            conflict_id=row["id"],
            kind=kind,
            ctype=row["conflict_type"],
            item=item,
            peer=peer,
            peer_source=peer_source,
            excerpt=row["user_rule_excerpt"] or "",
            recommended=_recommended_of(row["resolution_note"]),
            recommended_reason=row["user_rule_excerpt"] or "",
        ))

    run_id = load_latest_run_id(project_root, getattr(db, "db_path", None))
    if run_id:
        marker = f"bootstrap_run_id:{run_id}"
        async with conn.execute(
            "SELECT id, tags, source_url FROM knowledge_items "
            "WHERE project_id = ? AND status = 'pending_review'",
            (project_id,),
        ) as cur:
            pending = await cur.fetchall()
        extract_n = 0
        for prow in pending:
            if (prow["source_url"] or "") == "auto-extract":
                continue
            ptags = _parse_tags(prow["tags"])
            if marker not in ptags:
                continue
            if not _EXTRACT_SIGNALS.intersection(ptags):
                continue
            extract_n += 1
        if extract_n:
            cards.append(_extract_card(run_id, extract_n))
    return cards


def close_extract_runs(decisions: Optional[DecisionQueue], tags_rows: list[Any]) -> None:
    """知识审批/accept 成功后，按条目 tags 关闭 extract:<run_id>。"""
    if decisions is None:
        return
    for raw in tags_rows:
        for tag in _parse_tags(raw):
            if tag.startswith("bootstrap_run_id:"):
                decisions.close(f"extract:{tag.split(':', 1)[1]}")
