"""记忆健康车道：把整次召回的负反馈归到每一条命中记忆，并识别来源失效。

算法见 docs/memory-health.md。扫描只写 state/memory_health/*.yaml。
归档、改正文、supersedes 只发生在 resolve，或知识更新关闭 awaiting_edit 时。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from ..core.masking import mask_text
from ..injector.conflict import ConflictDetector
from ..memory.harvest import is_harvest_doc
from ..memory.logstore import iter_events
from ..memory.store import MemoryStore

logger = logging.getLogger(__name__)

WINDOW_DAYS = 14
NEUTRAL_OPEN = 3
CONTRADICT_ARCHIVE = 2
EVIDENCE_CAP = 10
EXCERPT_MAX = 200
NOTE_MAX = 200
SNOOZE_DAYS = 7

KINDS = ("incorrect", "stale_source")
STATUSES = ("open", "awaiting_edit", "snoozed", "resolved")
RESOLUTIONS = ("archive", "revise", "keep", "snooze")
_LIST_DEFAULT = ("open", "awaiting_edit")
_SHOW_STATUS = {"open": 0, "awaiting_edit": 1, "snoozed": 2}
_SHOW_KIND = {"incorrect": 0, "stale_source": 1}
_DOC_ID = re.compile(r"^[0-9a-f]{32}$")

RESOLVE_OPTIONS = (
    {"resolution": "archive", "effect": "记忆移入 archive；若有合格后继草稿则写 supersedes"},
    {"resolution": "revise", "effect": "发现改为 awaiting_edit，正文要另调 rsi_knowledge_update"},
    {"resolution": "keep", "effect": "关闭发现，低置信保留"},
    {"resolution": "snooze", "effect": "7 天内不再出卡"},
)


class _Hit:
    def __init__(
        self, *, token: str, action: str, rating: Optional[int],
        polarity: str, comment: str, ts: datetime,
    ) -> None:
        self.token = token
        self.action = action
        self.rating = rating
        self.polarity = polarity
        self.comment = comment
        self.ts = ts


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _relation(doc_type: str, polarity: str) -> str:
    """词表三分类：contradict / entail / neutral。禁止词优先已在 _polarity 里完成。"""
    if polarity == "neutral":
        return "neutral"
    if doc_type == "prohibition":
        return "contradict" if polarity == "permissive" else "entail"
    return "contradict" if polarity == "prohibitive" else "entail"


def _repo_file(root: Path, source_url: str) -> Optional[Path]:
    rel = (source_url or "").replace("\\", "/").strip()
    if not rel or "://" in rel:
        return None
    candidate = Path(rel)
    if candidate.is_absolute():
        return None
    root_resolved = root.resolve()
    path = (root_resolved / candidate).resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError:
        return None
    return path


def _fingerprint(path: Optional[Path]) -> str:
    if path is None or not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _health_dir(rsi_dir: Path) -> Path:
    return Path(rsi_dir) / "state" / "memory_health"


def _evidence(hits: list[_Hit]) -> list[dict[str, Any]]:
    ordered = sorted(hits, key=lambda hit: hit.ts)[-EVIDENCE_CAP:]
    rows: list[dict[str, Any]] = []
    for hit in ordered:
        row: dict[str, Any] = {
            "token": hit.token,
            "action": hit.action,
            "polarity": hit.polarity,
            "comment_excerpt": mask_text(hit.comment)[:EXCERPT_MAX],
            "ts": _iso(hit.ts),
        }
        if hit.rating is not None:
            row["rating"] = hit.rating
        rows.append(row)
    return rows


def _show_key(finding: dict[str, Any]) -> tuple[int, int]:
    return (
        _SHOW_STATUS.get(str(finding.get("status")), 9),
        _SHOW_KIND.get(str(finding.get("kind")), 9),
    )


class MemoryHealth:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._by_key: dict[tuple[str, str], dict[str, Any]] = {}

    def scan(self, project_root: Path, project_id: str = "") -> dict[str, Any]:
        """近 14 天负反馈 + 来源包含检查。单条失败跳过，整次扫描仍返回计数。"""
        self._by_key = {(f["memory_id"], f["kind"]): f for f in self._load() if f.get("memory_id") and f.get("kind")}
        stats: dict[str, Any] = {"opened": 0, "refreshed": 0, "closed": 0, "finding_ids": []}
        seen: set[tuple[str, str]] = set()
        grouped = self._negative_hits(project_id)
        root = Path(project_root)
        for doc in self._store.list_official(skip_harvest=True):
            if doc.status != "active" or is_harvest_doc(doc):
                continue
            try:
                self._scan_incorrect(doc, grouped.get(doc.id, []), stats, seen)
            except Exception:
                logger.warning("incorrect 扫描跳过 id=%s", doc.id, exc_info=True)
            try:
                self._scan_stale(doc, root, stats, seen)
            except Exception:
                logger.warning("stale_source 扫描跳过 id=%s", doc.id, exc_info=True)
        for finding in list(self._by_key.values()):
            if finding.get("status") == "resolved":
                continue
            key = (str(finding.get("memory_id")), str(finding.get("kind")))
            if key in seen:
                continue
            doc = self._read(str(finding.get("memory_id") or ""))
            if doc is None or doc.status != "active" or is_harvest_doc(doc):
                self._finish(finding, "resolved", "archive", "already_archived")
                stats["closed"] += 1
                stats["finding_ids"].append(finding["id"])
        return stats

    def list_findings(self, status: Optional[str] = None) -> dict[str, Any]:
        if status is not None and status not in STATUSES:
            return {"status": "error", "code": "invalid", "message": "status 不在枚举内"}
        wanted = (status,) if status else _LIST_DEFAULT
        rows = []
        for finding in self._load():
            if finding.get("status") not in wanted:
                continue
            doc = self._read(str(finding.get("memory_id") or ""))
            rows.append({
                "id": finding.get("id"),
                "memory_id": finding.get("memory_id"),
                "title": doc.title if doc is not None else "",
                "kind": finding.get("kind"),
                "status": finding.get("status"),
                "recommended": finding.get("recommended"),
                "evidence_count": len(finding.get("evidence") or []),
            })
        rows.sort(key=lambda row: str(row.get("id") or ""))
        return {"status": "success", "findings": rows}

    def explain(self, finding_id: str) -> dict[str, Any]:
        finding = self._find(finding_id)
        if finding is None:
            return {"status": "error", "code": "not_found", "message": "发现不存在"}
        doc = self._read(str(finding.get("memory_id") or ""))
        excerpt = ""
        title = ""
        doc_type = ""
        if doc is not None:
            title = doc.title
            doc_type = doc.type
            excerpt = (doc.content or "")[:EXCERPT_MAX]
        return {
            "status": "success",
            "finding": finding,
            "memory": {
                "id": finding.get("memory_id"),
                "title": title,
                "type": doc_type,
                "excerpt": excerpt,
            },
            "options": list(RESOLVE_OPTIONS),
        }

    def resolve(
        self, finding_id: str, resolution: str, note: str = "",
        project_root: Optional[Path] = None,
    ) -> dict[str, Any]:
        finding = self._find(finding_id)
        if finding is None:
            return {"status": "error", "code": "not_found", "message": "发现不存在"}
        if resolution not in RESOLUTIONS:
            return {"status": "error", "code": "invalid", "message": "resolution 不在枚举内"}
        if finding.get("status") == "resolved":
            return {"status": "error", "code": "invalid", "message": "发现已关闭"}
        note = (note or "")[:NOTE_MAX]
        if resolution == "archive":
            return self._archive(finding, note)
        if resolution == "revise":
            self._finish(finding, "awaiting_edit", "revise", note)
            return self._ok(finding)
        if resolution == "keep":
            self._freeze_fingerprint(finding, project_root)
            self._mark_dismissed(str(finding.get("memory_id") or ""))
            self._finish(finding, "resolved", "keep", note)
            return self._ok(finding)
        until = _iso(_now() + timedelta(days=SNOOZE_DAYS))
        finding["snooze_until"] = until
        self._finish(finding, "snoozed", "snooze", note)
        return self._ok(finding)

    def close_awaiting_edits(self, memory_id: str) -> int:
        """知识更新写到同一 id 后，关闭 awaiting_edit 并清掉当前低置信标记。"""
        closed = 0
        moment = _iso(_now())
        for finding in self._load():
            if finding.get("memory_id") != memory_id or finding.get("status") != "awaiting_edit":
                continue
            finding["status"] = "resolved"
            finding["resolution"] = "revise"
            finding["resolved_at"] = moment
            self._write(finding)
            closed += 1
        if closed:
            self._clear_low_confidence(memory_id)
        return closed

    def _negative_hits(self, project_id: str) -> dict[str, list[_Hit]]:
        since = _now() - timedelta(days=WINDOW_DAYS)
        events = list(iter_events(self._store.rsi_dir, all_rolls=True))
        feedback: dict[str, dict[str, Any]] = {}
        for event in events:
            token = event.get("token")
            if event.get("kind") == "feedback" and token:
                feedback[str(token)] = event
        grouped: dict[str, list[_Hit]] = {}
        for event in events:
            if event.get("kind") not in (None, "recall"):
                continue
            moment = _parse_ts(event.get("ts") or event.get("created_at"))
            if moment is None or moment < since:
                continue
            if project_id and event.get("project_id") and event.get("project_id") != project_id:
                continue
            token = str(event.get("token") or "")
            fb = feedback.get(token) if token else None
            action = event.get("action") or event.get("feedback_action")
            if not action and fb:
                action = fb.get("action") or fb.get("feedback_action")
            rating = event.get("rating", event.get("feedback_rating"))
            if rating is None and fb:
                rating = fb.get("rating", fb.get("feedback_rating"))
            try:
                rating_n = int(rating) if rating is not None else None
            except (TypeError, ValueError):
                rating_n = None
            comment = event.get("comment") or event.get("feedback_comment")
            if not comment and fb:
                comment = fb.get("comment") or fb.get("feedback_comment")
            comment = str(comment or "").strip()
            if not comment:
                continue
            if action != "rejected" and not (rating_n is not None and rating_n <= 2):
                continue
            hit = _Hit(
                token=token,
                action=str(action or ""),
                rating=rating_n,
                polarity=ConflictDetector._polarity(comment),
                comment=comment,
                ts=moment,
            )
            for raw in event.get("retrieved") or []:
                doc_id = str(raw or "").strip().lower()
                if _DOC_ID.fullmatch(doc_id):
                    grouped.setdefault(doc_id, []).append(hit)
        return grouped

    def _scan_incorrect(self, doc: Any, hits: list[_Hit], stats: dict[str, Any], seen: set[tuple[str, str]]) -> None:
        key = (doc.id, "incorrect")
        existing = self._by_key.get(key)
        if existing and self._snoozed(existing):
            seen.add(key)
            return
        if existing and existing.get("status") == "awaiting_edit":
            seen.add(key)
            return
        if existing and existing.get("status") == "resolved" and existing.get("resolution") != "keep":
            seen.add(key)
            return
        suspects = [hit for hit in hits if _relation(doc.type, hit.polarity) == "contradict"]
        neutrals = [hit for hit in hits if _relation(doc.type, hit.polarity) == "neutral"]
        if existing and existing.get("status") == "resolved" and existing.get("resolution") == "keep":
            resolved_at = _parse_ts(existing.get("resolved_at"))
            suspects = [hit for hit in suspects if resolved_at is None or hit.ts > resolved_at]
            neutrals = []
            if not suspects:
                seen.add(key)
                return
        if suspects:
            chosen = suspects
            if len(suspects) >= CONTRADICT_ARCHIVE:
                recommended = "archive"
                reason = f"14 天内 {len(suspects)} 条评语与该记忆极性相反"
            else:
                recommended = "revise"
                reason = "14 天内 1 条评语与该记忆极性相反"
        elif len(neutrals) >= NEUTRAL_OPEN:
            chosen = neutrals
            recommended = "revise"
            reason = f"14 天内 {len(neutrals)} 条中性负反馈"
        else:
            if existing and existing.get("status") in ("open", "snoozed"):
                self._finish(existing, "resolved", "keep", "window_expired")
                stats["closed"] += 1
                stats["finding_ids"].append(existing["id"])
            if existing:
                seen.add(key)
            return
        self._upsert(
            existing, memory_id=doc.id, kind="incorrect", evidence=_evidence(chosen),
            recommended=recommended, reason=reason, extra={}, stats=stats, seen=seen,
        )

    def _scan_stale(self, doc: Any, root: Path, stats: dict[str, Any], seen: set[tuple[str, str]]) -> None:
        key = (doc.id, "stale_source")
        existing = self._by_key.get(key)
        source = str((doc.extra or {}).get("source_url") or "")
        if not source:
            if existing and existing.get("status") in ("open", "snoozed"):
                self._finish(existing, "resolved", "keep", "source_restored")
                stats["closed"] += 1
                stats["finding_ids"].append(existing["id"])
                seen.add(key)
            elif existing:
                seen.add(key)
            return
        path = _repo_file(root, source)
        if path is None:
            if existing:
                seen.add(key)
            return
        if existing and self._snoozed(existing):
            seen.add(key)
            return
        if existing and existing.get("status") == "awaiting_edit":
            seen.add(key)
            return
        if existing and existing.get("status") == "resolved" and existing.get("resolution") != "keep":
            seen.add(key)
            return
        fingerprint = _fingerprint(path)
        holds, state = self._stale_holds(doc, path, fingerprint)
        if existing and existing.get("status") == "resolved" and existing.get("resolution") == "keep":
            if fingerprint == existing.get("source_fingerprint") or not holds:
                seen.add(key)
                return
        if not holds:
            if existing and existing.get("status") in ("open", "snoozed"):
                self._finish(existing, "resolved", "keep", "source_restored")
                stats["closed"] += 1
                stats["finding_ids"].append(existing["id"])
            if existing:
                seen.add(key)
            return
        reason = "来源文件已不在工作区" if state == "missing" else "来源已变且标题短语已不在文件中"
        self._upsert(
            existing, memory_id=doc.id, kind="stale_source", evidence=[],
            recommended="archive", reason=reason,
            extra={"source_url": source, "source_state": state, "source_fingerprint": fingerprint},
            stats=stats, seen=seen,
        )

    def _stale_holds(self, doc: Any, path: Path, fingerprint: str) -> tuple[bool, str]:
        if not path.is_file():
            return True, "missing"
        extra = doc.extra or {}
        stored = str(extra.get("source_hash") or "")
        if stored:
            changed = fingerprint != stored
        else:
            updated = _parse_ts(doc.updated_at)
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            changed = updated is not None and mtime > updated
        if not changed:
            return False, ""
        phrase = ConflictDetector._key_phrase(doc.title or "")
        if len(phrase) < 2:
            return False, ""
        text = path.read_text(encoding="utf-8", errors="replace")
        if ConflictDetector._find_phrase(text, phrase) < 0:
            return True, "phrase_absent"
        return False, ""

    def _upsert(
        self, existing: Optional[dict[str, Any]], *, memory_id: str, kind: str,
        evidence: list[dict[str, Any]], recommended: str, reason: str,
        extra: dict[str, Any], stats: dict[str, Any], seen: set[tuple[str, str]],
    ) -> None:
        key = (memory_id, kind)
        if existing is None:
            finding = {
                "id": uuid.uuid4().hex,
                "memory_id": memory_id,
                "kind": kind,
                "status": "open",
                "recommended": recommended,
                "recommended_reason": reason[:NOTE_MAX],
                "resolution": "",
                "evidence": evidence,
                "detected_at": _iso(_now()),
            }
            finding.update(extra)
            self._write(finding)
            self._by_key[key] = finding
            stats["opened"] += 1
            stats["finding_ids"].append(finding["id"])
            seen.add(key)
            return
        same = (
            existing.get("status") == "open"
            and existing.get("recommended") == recommended
            and existing.get("recommended_reason") == reason[:NOTE_MAX]
            and existing.get("evidence") == evidence
            and all(existing.get(name) == extra[name] for name in extra)
        )
        if same:
            seen.add(key)
            return
        reopened = existing.get("status") == "resolved"
        existing["status"] = "open"
        existing["recommended"] = recommended
        existing["recommended_reason"] = reason[:NOTE_MAX]
        existing["evidence"] = evidence
        if reopened:
            existing["resolution"] = ""
            existing.pop("resolved_at", None)
        existing.update(extra)
        self._write(existing)
        stats["opened" if reopened else "refreshed"] += 1
        stats["finding_ids"].append(existing["id"])
        seen.add(key)

    def _archive(self, finding: dict[str, Any], note: str) -> dict[str, Any]:
        memory_id = str(finding.get("memory_id") or "")
        doc = self._read(memory_id)
        if doc is None or doc.status != "active":
            self._finish(finding, "resolved", "archive", note or "already_archived")
            return {
                "status": "error", "code": "conflict", "message": "记忆已不在正式库",
                "finding_id": finding.get("id"), "state": "resolved", "resolution": "archive",
            }
        try:
            self._store.move(memory_id, self._store.rsi_dir / "memory" / "archive")
        except FileNotFoundError:
            self._finish(finding, "resolved", "archive", note or "already_archived")
            return {
                "status": "error", "code": "conflict", "message": "记忆已不在正式库",
                "finding_id": finding.get("id"), "state": "resolved", "resolution": "archive",
            }
        successor_id = self._link_successor(memory_id, finding.get("evidence") or [])
        if successor_id:
            finding["successor_id"] = successor_id
        self._finish(finding, "resolved", "archive", note)
        payload = self._ok(finding)
        payload["archived_id"] = memory_id
        if successor_id:
            payload["successor_id"] = successor_id
        return payload

    def _link_successor(self, memory_id: str, evidence: Iterable[dict[str, Any]]) -> Optional[str]:
        moments = [moment for moment in (_parse_ts(row.get("ts")) for row in evidence) if moment]
        if not moments:
            return None
        floor = min(moments)
        best = None
        best_at: Optional[datetime] = None
        for doc in self._store.list_pending():
            if memory_id not in (doc.refs or []):
                continue
            created = _parse_ts(doc.created_at)
            if created is None or created < floor:
                continue
            if best is None or (best_at is not None and created >= best_at):
                best = doc
                best_at = created
        if best is None:
            return None
        try:
            from ..memory.graph import add_edge

            add_edge(self._store.rsi_dir, best.id, memory_id, "supersedes")
        except (OSError, ValueError):
            logger.warning("supersedes 写入失败 memory=%s", memory_id, exc_info=True)
            return None
        return best.id

    def _freeze_fingerprint(self, finding: dict[str, Any], project_root: Optional[Path]) -> None:
        if finding.get("kind") != "stale_source" or project_root is None:
            return
        doc = self._read(str(finding.get("memory_id") or ""))
        if doc is None:
            return
        source = str((doc.extra or {}).get("source_url") or finding.get("source_url") or "")
        path = _repo_file(Path(project_root), source)
        finding["source_fingerprint"] = _fingerprint(path)

    def _mark_dismissed(self, memory_id: str) -> None:
        doc = self._read(memory_id)
        if doc is None or doc.status != "active" or not doc.path:
            return
        extra = dict(doc.extra or {})
        extra["health_dismissed_at"] = _iso(_now())
        self._store.write(doc.model_copy(update={"extra": extra}), dest=self._store.rsi_dir / doc.path)

    def _clear_low_confidence(self, memory_id: str) -> None:
        doc = self._read(memory_id)
        if doc is None or not doc.path:
            return
        extra = dict(doc.extra or {})
        extra.pop("low_confidence", None)
        extra.pop("low_confidence_reason", None)
        extra.pop("low_confidence_at", None)
        self._store.write(doc.model_copy(update={"extra": extra}), dest=self._store.rsi_dir / doc.path)

    def _snoozed(self, finding: dict[str, Any]) -> bool:
        if finding.get("status") != "snoozed":
            return False
        until = _parse_ts(finding.get("snooze_until"))
        return until is not None and until > _now()

    def _finish(self, finding: dict[str, Any], state: str, resolution: str, note: str) -> None:
        finding["status"] = state
        finding["resolution"] = resolution
        if note:
            finding["note"] = note[:NOTE_MAX]
        if state == "resolved":
            finding["resolved_at"] = _iso(_now())
        self._write(finding)
        if finding.get("memory_id") and finding.get("kind"):
            self._by_key[(str(finding["memory_id"]), str(finding["kind"]))] = finding

    def _ok(self, finding: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "success",
            "finding_id": finding.get("id"),
            "state": finding.get("status"),
            "resolution": finding.get("resolution"),
        }

    def _load(self) -> list[dict[str, Any]]:
        root = _health_dir(self._store.rsi_dir)
        if not root.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(root.glob("*.yaml")):
            if path.name.endswith(".tmp"):
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(data, dict) and data.get("id"):
                rows.append(data)
        return rows

    def _find(self, finding_id: str) -> Optional[dict[str, Any]]:
        for finding in self._load():
            if finding.get("id") == finding_id:
                return finding
        return None

    def _read(self, memory_id: str) -> Any:
        if not memory_id:
            return None
        try:
            return self._store.read(memory_id)
        except (FileNotFoundError, ValueError):
            return None

    def _write(self, finding: dict[str, Any]) -> None:
        dest = _health_dir(self._store.rsi_dir) / f"{finding['id']}.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(finding, allow_unicode=True, sort_keys=False)
        tmp = Path(str(dest) + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, dest)


def incorrect_evidence_tokens(rsi_dir: Path) -> set[str]:
    """已被 incorrect 发现收录的反馈 token。提案模板不再用这些评语复制禁止项。"""
    tokens: set[str] = set()
    root = _health_dir(rsi_dir)
    if not root.is_dir():
        return tokens
    for path in root.glob("*.yaml"):
        if path.name.endswith(".tmp"):
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict) or data.get("kind") != "incorrect":
            continue
        for row in data.get("evidence") or []:
            if isinstance(row, dict) and row.get("token"):
                tokens.add(str(row["token"]))
    return tokens


def recall_index(rsi_dir: Path) -> dict[str, dict[str, Any]]:
    """每个记忆最多一条展示用发现：open 优先于 awaiting_edit，再优先于 snoozed。"""
    best: dict[str, dict[str, Any]] = {}
    root = _health_dir(rsi_dir)
    if not root.is_dir():
        return best
    for path in root.glob("*.yaml"):
        if path.name.endswith(".tmp"):
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict) or data.get("status") not in _SHOW_STATUS:
            continue
        memory_id = str(data.get("memory_id") or "")
        if not memory_id:
            continue
        current = best.get(memory_id)
        if current is None or _show_key(data) < _show_key(current):
            best[memory_id] = data
    return best


def demote_for_health(docs: list[Any], index: dict[str, dict[str, Any]]) -> list[Any]:
    """稳定分段：无标记、仅低置信、open 或 awaiting_edit。snoozed 不降权。"""
    from .governance import is_low_confidence

    def tier(doc: Any) -> int:
        finding = index.get(doc.id)
        if finding and finding.get("status") in ("open", "awaiting_edit"):
            return 2
        if is_low_confidence(doc):
            return 1
        return 0

    return sorted(docs, key=tier)


def attach_health(payload: dict[str, Any], doc: Any, index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    finding = index.get(doc.id)
    if not finding or finding.get("status") not in _SHOW_STATUS:
        return payload
    payload["health"] = {
        "finding_id": finding.get("id"),
        "kind": finding.get("kind"),
        "status": finding.get("status"),
        "recommended": finding.get("recommended"),
    }
    return payload


def close_awaiting_edits(store: MemoryStore, memory_id: str) -> int:
    return MemoryHealth(store).close_awaiting_edits(memory_id)


def health_decision_cards(store: MemoryStore) -> list[Any]:
    """只为 open 发现出卡。调用方把优先级放在规则冲突和抽取卡之后。"""
    from ..services.decision_queue import DecisionCard

    cards = []
    health = MemoryHealth(store)
    for finding in health._load():
        if finding.get("status") != "open":
            continue
        doc = health._read(str(finding.get("memory_id") or ""))
        title = doc.title if doc is not None else str(finding.get("memory_id") or "")
        excerpt = ((doc.content if doc is not None else "") or "")[:EXCERPT_MAX]
        comments = "；".join(
            str(row.get("comment_excerpt") or "")
            for row in (finding.get("evidence") or [])
            if isinstance(row, dict) and row.get("comment_excerpt")
        )[:EXCERPT_MAX]
        kind = str(finding.get("kind") or "")
        if kind == "stale_source":
            prompt = f"记忆「{title}」的来源已经对不上。归档、改正文，还是先留着？"
        else:
            prompt = f"记忆「{title}」被评语打过脸。归档、改正文，还是先留着？"
        cards.append(DecisionCard(
            id=str(finding.get("id") or ""),
            kind="memory_health",
            prompt=prompt,
            options=[
                {"id": "archive", "label": "归档这条记忆", "resolution": "archive"},
                {"id": "revise", "label": "改正文后再关闭", "resolution": "revise"},
                {"id": "keep", "label": "保留，这条记忆仍成立", "resolution": "keep"},
                {"id": "snooze", "label": "7 天内不再问", "resolution": "snooze"},
            ],
            recommended=str(finding.get("recommended") or "revise"),
            recommended_reason=str(finding.get("recommended_reason") or ""),
            sides=[
                {"role": "memory", "title": title, "excerpt": excerpt, "source": str(finding.get("source_url") or "")},
                {"role": "evidence", "title": kind, "excerpt": comments, "source": ""},
            ],
            impact={
                "recall": "归档后这条不再召回；保留则继续召回并带低置信",
                "inject": "归档后下一轮注入不再带上这条",
                "code_hint": "",
            },
        ))
    return cards
