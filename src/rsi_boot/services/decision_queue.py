"""对话内抉择队列：进程内 sticky / suppress / close，并收集召回决策卡。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from ..cli.knowledge_accept import iter_run_extracts, load_latest_run_id
from ..memory.store import MemoryStore

_HISTORICAL_ID_RE = re.compile(r"historical_id=([^\s]+)")

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


def _recommended_of(note: str | None, conflict_type: str = "") -> str:
    raw = note or ""
    if raw.startswith("recommended:"):
        token = raw.split(":", 1)[1].strip().split()[0]
        if token:
            return token
    if conflict_type == "version":
        return "keep_peer"
    return "keep_item"


def _historical_id_from_excerpt(excerpt: str) -> Optional[str]:
    match = _HISTORICAL_ID_RE.search(excerpt or "")
    return match.group(1) if match else None


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


def _extract_card(run_id: str, pending_items: list[dict[str, Any]]) -> DecisionCard:
    pending_n = len(pending_items)
    sides = [
        {
            "role": "extract",
            "item_id": item.get("id") or "",
            "title": item.get("title") or "",
            "excerpt": _excerpt(item.get("content") or ""),
            "source": item.get("source_url") or "",
        }
        for item in pending_items
    ]
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
        sides=sides,
        impact={
            "recall": "批准后这些抽取进入召回；拒绝则丢弃",
            "inject": "生效后可能写入 rsi-*.mdc / AGENTS.md",
            "code_hint": "",
            "run_id": run_id,
        },
    )


async def collect_decision_cards(
    store: MemoryStore,
    project_id: str,
    *,
    project_root: Optional[Path] = None,
) -> list[DecisionCard]:
    """收集当前可问的抉择卡（未 pick、未算 more_waiting）。"""
    return await _collect_decision_cards_store(store, project_id, project_root=project_root)


def _doc_source_url(doc: Any) -> str:
    extra = getattr(doc, "extra", None) or {}
    return str(extra.get("source_url") or getattr(doc, "source", None) or "")


def _doc_as_item(doc: Any) -> dict[str, Any]:
    return {
        "id": doc.id,
        "title": doc.title,
        "content": doc.content,
        "source_url": _doc_source_url(doc),
        "status": doc.status,
        "tags": list(doc.tags or []),
    }


def _read_doc(store: Any, item_id: str) -> Any | None:
    if not item_id:
        return None
    try:
        return store.read(item_id)
    except (FileNotFoundError, ValueError):
        return None


def _peer_from_store(store: Any, source: str, excerpt: str = "") -> Optional[dict[str, Any]]:
    norm = (source or "").replace("\\", "/")
    if norm.startswith("item:"):
        doc = _read_doc(store, norm[5:])
        return _doc_as_item(doc) if doc is not None else None
    if "#" in norm:
        _url, suffix = norm.rsplit("#", 1)
        doc = _read_doc(store, suffix)
        if doc is not None:
            return _doc_as_item(doc)
    hist_id = _historical_id_from_excerpt(excerpt)
    if hist_id:
        doc = _read_doc(store, hist_id)
        if doc is not None:
            return _doc_as_item(doc)
    return None


async def _collect_decision_cards_store(
    store: Any, project_id: str, *, project_root: Optional[Path] = None,
) -> list[DecisionCard]:
    path = store.rsi_dir / "state" / "conflicts.yaml"
    rows: list[dict[str, Any]] = []
    if path.is_file():
        try:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, Exception):
            data = None
        if isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            rows = [r for r in (data.get("conflicts") or []) if isinstance(r, dict)]

    cards: list[DecisionCard] = []
    for row in rows:
        if row.get("status", "open") != "open":
            continue
        ctype = row.get("conflict_type") or row.get("type") or ""
        if ctype not in ("incoherent", "version", "doc_code"):
            continue
        item_id = row.get("item_id") or ""
        doc = _read_doc(store, item_id)
        item = _doc_as_item(doc) if doc is not None else {
            "id": item_id, "title": "", "content": "", "source_url": "",
            "status": "", "tags": [],
        }
        tags = _parse_tags(item["tags"])
        source = item["source_url"]
        if source == "auto-extract" or not _has_bootstrap_run_tag(tags):
            kind = "daily_conflict"
        else:
            kind = "bootstrap_conflict"
        peer_source = (row.get("user_rule_path") or "").replace("\\", "/")
        excerpt = row.get("user_rule_excerpt") or row.get("excerpt") or ""
        peer = _peer_from_store(store, peer_source, excerpt)
        cards.append(_conflict_card(
            conflict_id=row.get("id") or "",
            kind=kind,
            ctype=ctype,
            item=item,
            peer=peer,
            peer_source=peer_source,
            excerpt=excerpt,
            recommended=_recommended_of(row.get("resolution_note"), ctype),
            recommended_reason=excerpt,
        ))

    run_id = load_latest_run_id(project_root, store.rsi_dir / "bootstrap_run.json")
    if run_id:
        extracts = [_doc_as_item(doc) for doc in iter_run_extracts(store, run_id)]
        if extracts:
            cards.append(_extract_card(run_id, extracts))
    return cards


async def close_extract_runs(
    decisions: Optional[DecisionQueue],
    tags_rows: list[Any],
    *,
    store: Any = None,
    project_id: str = "",
) -> None:
    """知识审批成功后，仅当该 run 无剩余 pending 抽取时关闭 extract:<run_id>。"""
    del project_id
    if decisions is None:
        return
    run_ids: set[str] = set()
    for raw in tags_rows:
        for tag in _parse_tags(raw):
            if tag.startswith("bootstrap_run_id:"):
                run_ids.add(tag.split(":", 1)[1])
    for run_id in run_ids:
        remaining = _pending_extract_count_store(store, run_id) if store is not None else 0
        if remaining == 0:
            decisions.close(f"extract:{run_id}")


def _pending_extract_count_store(store: Any, run_id: str) -> int:
    return len(iter_run_extracts(store, run_id))
