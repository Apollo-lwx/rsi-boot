"""知识提取（Spec v3.0 §4.4）：候选汇集 → 规则式提取（默认，零 Key）→ 标题去重 → 人工确认队列。

流程：
1. 候选汇集：modified diff>20% 与 rejected+comment（反馈时由 FeedbackWorker 写入）+
   rating ≥ 4 的高质量交互（每日任务从 interaction_logs 汇集，UNIQUE(source_log_id, type) 幂等）
2. 规则式提取（默认）：modified→convention（修改稿沉淀）；rejected+comment→prohibition
   （comment 前缀规范化「禁止：」）；rating≥4→convention（问答对直接沉淀）
3. 去重：标题完全匹配 → 合并（刷新 updated_at）；无语义去重（embedding 属增强层）
4. 人工确认：草稿以 status='pending_review' 入库（不生成 embedding、不入检索），
   经 rsi_knowledge_review 确认后转 active；拒绝转 rejected，留存 30 天后清理

LLM 提取与向量去重为附录 C 可选增强（enhance.extract_llm: true 时启用）。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..data.sqlite import SQLiteClient
from ..injector.conflict import ConflictDetector
from ..knowledge.embedding import EmbeddingService, deserialize_embedding
from ..memory.logstore import iter_events
from ..memory.paths import pending_dir
from ..memory.store import MemoryStore, memory_filename
from ..memory.types import MemoryDoc
from ..scanner.conflict_gate import DraftItem, gate_drafts
from .pipeline import extract_draft_rule as extract_draft_rule_fn
from .pipeline import extract_from_feedback as extract_from_feedback_fn

logger = logging.getLogger(__name__)

_EXTRACT_MODEL_DEFAULT = "gpt-4o-mini"
_DEDUP_COSINE_THRESHOLD = 0.9   # 增强层：cosine ≥ 0.9 视为重复
_REJECTED_RETENTION_DAYS = 30   # rejected 留存 30 天后清理
_HIGH_RATED_MIN_RATING = 4      # rating ≥ 4 进入候选
_HIGH_RATED_WINDOW_HOURS = 24   # 每日任务汇集窗口

_EXTRACT_PROMPT = """你是知识工程师。从下面的（用户问题, 高质量回答/修改后内容）对中提炼一条「可复用的经验规则」——
泛化的做法、约束或坑点，而非本次交互的流水账。若内容不具备复用价值，输出 {{"skip": true}}。

【用户问题】
{question}

【回答/修改后内容】
{answer}

仅输出 JSON：
{{"title": "≤30 字标题", "content": "经验规则正文（≤500 字）", "tags": ["≤5 个标签"],
  "domain": "领域或 null", "roles": ["适用角色，空数组=通用"]}}
或 {{"skip": true}}"""


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeExtractor:
    def __init__(
        self,
        db: Optional[SQLiteClient] = None,
        embedding: Optional[EmbeddingService] = None,
        adapter: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
        store: Optional[Any] = None,
        rsi_dir: Optional[Path] = None,
    ):
        if db is None and store is None and rsi_dir is None:
            raise TypeError("db is required when store and rsi_dir are omitted")
        self._db = db
        self._embedding = embedding
        self._adapter = adapter
        self._store = store
        if rsi_dir is not None:
            self._rsi_dir = Path(rsi_dir)
        elif store is not None:
            self._rsi_dir = Path(store.rsi_dir)
        else:
            self._rsi_dir = None
        config = config or {}
        enhance = config.get("enhance", {})
        # 零 Key 默认：规则式提取；LLM 提取需 enhance.extract_llm + adapter 可用
        self._use_llm = bool(enhance.get("extract_llm")) and adapter is not None
        self._model = str(enhance.get("model", {}).get("default", _EXTRACT_MODEL_DEFAULT))

    # ---- 1. 候选汇集 ----

    async def collect_high_rated(self, since_hours: int = _HIGH_RATED_WINDOW_HOURS) -> int:
        """rating ≥ 4 且有响应摘录的交互汇入候选队列（幂等），返回新增数"""
        if self._db is None:
            return 0
        conn = await self._db.connect()
        since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
        async with conn.execute(
            "SELECT id, project_id, raw_input, response_excerpt FROM interaction_logs "
            "WHERE feedback_rating >= ? AND response_excerpt IS NOT NULL AND created_at >= ?",
            (_HIGH_RATED_MIN_RATING, since),
        ) as cur:
            rows = await cur.fetchall()
        inserted = 0
        for row in rows:
            cur = await conn.execute(
                "INSERT OR IGNORE INTO extraction_candidates"
                " (id, project_id, source_log_id, candidate_type, question, answer, status, created_at)"
                " VALUES (?, ?, ?, 'high_rated', ?, ?, 'pending', ?)",
                (
                    uuid.uuid4().hex, row["project_id"] or "default", row["id"],
                    row["raw_input"], row["response_excerpt"], _utc_iso(),
                ),
            )
            inserted += cur.rowcount
        await conn.commit()
        return inserted

    # ---- 2a. 规则式提取（默认，零 Key，§4.4） ----

    @staticmethod
    def _summarize(text: str, limit: int) -> str:
        """取首行/首句作为标题摘要，超长截断"""
        from .pipeline import summarize_title
        return summarize_title(text, limit)

    def extract_draft_rule(self, candidate_type: str, question: str, answer: str) -> Optional[Dict[str, Any]]:
        """三类候选的规则式草稿生成；无提取价值（内容为空）返回 None"""
        return extract_draft_rule_fn(candidate_type, question, answer)

    def extract_from_feedback(
        self,
        action: str,
        comment: str | None = None,
        retrieved: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> List[Path]:
        """有 store/rsi_dir 时委托 pipeline 只写 pending YAML；否则返回空。"""
        if self._rsi_dir is None:
            return []
        return extract_from_feedback_fn(
            self._rsi_dir,
            action=action,
            comment=comment,
            retrieved=retrieved,
            **kwargs,
        )

    async def find_title_duplicate(self, project_id: str, title: str) -> Optional[str]:
        """规则式去重（§4.4）：标题完全匹配 active/pending_review 条目 → 返回已有条目 id"""
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT id FROM knowledge_items "
            "WHERE project_id = ? AND title = ? AND status IN ('active', 'pending_review')",
            (project_id, title),
        ) as cur:
            row = await cur.fetchone()
        return row["id"] if row else None

    # ---- 2b. LLM 提取（附录 C 可选增强） ----

    async def extract_draft(self, question: str, answer: str) -> Optional[Dict[str, Any]]:
        """LLM 提取知识草稿；skip 或解析失败返回 None"""
        messages = [{"role": "user", "content": _EXTRACT_PROMPT.format(
            question=question[:2000], answer=answer[:4000],
        )}]
        try:
            result = await self._adapter.complete(self._model, messages)
            data = json.loads(result.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
        except Exception as exc:
            logger.warning("知识提取 LLM 调用/解析失败: %s", exc)
            return None
        if data.get("skip"):
            return None
        title = str(data.get("title", "")).strip()
        content = str(data.get("content", "")).strip()
        if not title or not content:
            return None
        return {
            "title": title[:100],
            "content": content[:2000],
            "tags": [str(t)[:30] for t in data.get("tags") or []][:5],
            "domain": data.get("domain") or None,
            "roles": [str(r)[:50] for r in data.get("roles") or []][:10],
        }

    # ---- 3. 向量去重 ----

    async def find_duplicate(self, project_id: str, draft_vector: np.ndarray) -> Optional[str]:
        """草稿向量与现有 active/pending_review 条目比对，cosine ≥ 0.9 返回已有条目 id"""
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT id, embedding FROM knowledge_items "
            "WHERE project_id = ? AND status IN ('active', 'pending_review') AND embedding IS NOT NULL",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
        norm = float(np.linalg.norm(draft_vector))
        if norm < 1e-12:
            return None
        for row in rows:
            existing = deserialize_embedding(row["embedding"])
            denom = norm * float(np.linalg.norm(existing))
            if denom < 1e-12:
                continue
            if float(np.dot(draft_vector, existing) / denom) >= _DEDUP_COSINE_THRESHOLD:
                return row["id"]
        return None

    # ---- 4. 每日任务主流程 ----

    def _store_or_dir(self) -> MemoryStore | None:
        if self._store is not None:
            return self._store
        if self._rsi_dir is not None:
            return MemoryStore(self._rsi_dir)
        return None

    def _high_rated_seen_ids(self, rsi_dir: Path) -> set[str]:
        path = Path(rsi_dir) / "logs" / "candidates.jsonl"
        seen: set[str] = set()
        if not path.is_file():
            return seen
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("candidate_type") != "high_rated":
                continue
            sid = rec.get("source_event_id") or rec.get("id")
            if sid:
                seen.add(str(sid))
        return seen

    def _append_high_rated_candidate(self, rsi_dir: Path, event_id: str, path: str) -> None:
        dest = Path(rsi_dir) / "logs" / "candidates.jsonl"
        dest.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": _utc_iso(),
            "kind": "candidate",
            "candidate_type": "high_rated",
            "source_event_id": event_id,
            "path": path,
            "status": "extracted",
        }
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def _run_daily_store(self) -> Dict[str, int]:
        stats = {"collected": 0, "extracted": 0, "merged": 0, "skipped": 0, "cleaned": 0}
        store = self._store_or_dir()
        if store is None:
            return stats
        seen = self._high_rated_seen_ids(store.rsi_dir)
        since = datetime.now(timezone.utc) - timedelta(hours=_HIGH_RATED_WINDOW_HOURS)
        since_key = since.strftime("%Y-%m-%dT%H:%M:%S")
        for ev in iter_events(store.rsi_dir):
            rating = ev.get("rating")
            try:
                rating_n = int(rating) if rating is not None else 0
            except (TypeError, ValueError):
                rating_n = 0
            if rating_n < _HIGH_RATED_MIN_RATING:
                continue
            ts = str(ev.get("ts") or "")
            if ts and ts.replace("Z", "")[:19] < since_key:
                continue
            answer = str(ev.get("excerpt") or ev.get("content") or "").strip()
            question = str(ev.get("task") or ev.get("raw_input") or "")
            if not answer:
                continue
            eid = str(ev.get("id") or "")
            if eid and eid in seen:
                stats["skipped"] += 1
                continue
            stats["collected"] += 1
            draft = self.extract_draft_rule("high_rated", question, answer)
            if draft is None:
                stats["skipped"] += 1
                continue
            doc = MemoryDoc(
                id=uuid.uuid4().hex,
                type="convention",
                title=str(draft["title"])[:120],
                content=str(draft["content"])[:20000],
                source="auto-extract",
                extra={"candidate_type": "high_rated", "source_event_id": eid},
            )
            dest = pending_dir(store.rsi_dir, "convention") / memory_filename(doc.title, doc.id)
            written = store.write(doc, dest=dest)
            if eid:
                self._append_high_rated_candidate(store.rsi_dir, eid, written.path or "")
                seen.add(eid)
            stats["extracted"] += 1
        return stats

    async def run_daily(self) -> Dict[str, int]:
        """汇集 → 提取（规则式默认 / LLM 增强）→ 去重 → pending_review 入库；返回各环节计数"""
        stats = {"collected": 0, "extracted": 0, "merged": 0, "skipped": 0, "cleaned": 0}
        if self._db is None:
            return await self._run_daily_store()
        stats["collected"] = await self.collect_high_rated()

        conn = await self._db.connect()
        async with conn.execute(
            "SELECT id, project_id, candidate_type, question, answer FROM extraction_candidates "
            "WHERE status = 'pending' ORDER BY created_at LIMIT 50",
        ) as cur:
            candidates = await cur.fetchall()

        for cand in candidates:
            if self._use_llm:
                draft = await self.extract_draft(cand["question"], cand["answer"])
            else:
                draft = self.extract_draft_rule(cand["candidate_type"], cand["question"], cand["answer"])
            now = _utc_iso()
            if draft is None:
                await conn.execute(
                    "UPDATE extraction_candidates SET status = 'dismissed', processed_at = ? WHERE id = ?",
                    (now, cand["id"]),
                )
                stats["skipped"] += 1
                continue

            # 去重：规则式走标题完全匹配；LLM 增强走向量 cosine ≥ 0.9（需 embedding 可用）
            if self._use_llm and self._embedding is not None:
                draft_vector = await self._embedding.embed_one(f"{draft['title']}\n{draft['content']}")
                duplicate_id = (
                    await self.find_duplicate(cand["project_id"], draft_vector)
                    if draft_vector is not None else None
                )
            else:
                duplicate_id = await self.find_title_duplicate(cand["project_id"], draft["title"])
            if duplicate_id is not None:
                await conn.execute(
                    "UPDATE knowledge_items SET updated_at = ? WHERE id = ?", (now, duplicate_id)
                )
                stats["merged"] += 1
            else:
                new_id = uuid.uuid4().hex
                await conn.execute(
                    "INSERT INTO knowledge_items"
                    " (id, project_id, title, content, content_type, roles, domain, tags,"
                    "  source_url, status, embedding, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'auto-extract', 'pending_review', NULL, ?, ?)",
                    (
                        new_id, cand["project_id"], draft["title"], draft["content"],
                        draft.get("content_type", "convention"),
                        json.dumps(draft["roles"], ensure_ascii=False), draft["domain"],
                        json.dumps(draft["tags"], ensure_ascii=False), now, now,
                    ),
                )
                stats["extracted"] += 1
                await self._persist_incoherent_vs_actives(cand["project_id"], new_id, draft)
            await conn.execute(
                "UPDATE extraction_candidates SET status = 'extracted', processed_at = ? WHERE id = ?",
                (now, cand["id"]),
            )
        await conn.commit()

        stats["cleaned"] = await self.cleanup_rejected()
        if any(stats.values()):
            logger.info("知识提取每日任务完成：%s", stats)
        return stats

    @staticmethod
    def _peer_source_url(peer_id: str, raw_url: Optional[str], url_counts: Dict[str, int]) -> str:
        norm = (raw_url or "").replace("\\", "/")
        if not norm:
            return f"item:{peer_id}"
        if url_counts.get(norm, 0) > 1:
            return f"{norm}#{peer_id}"
        return norm

    async def _load_active_peers(self, project_id: str) -> List[Tuple[str, DraftItem]]:
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT id, title, content, content_type, source_url, tags FROM knowledge_items "
            "WHERE project_id = ? AND status = 'active' "
            "AND content_type IN ('convention', 'prohibition', 'experience')",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
        url_counts: Dict[str, int] = {}
        for row in rows:
            norm = (row["source_url"] or "").replace("\\", "/")
            url_counts[norm] = url_counts.get(norm, 0) + 1
        peers: List[Tuple[str, DraftItem]] = []
        for row in rows:
            tags: List[str] = []
            if row["tags"]:
                try:
                    loaded = json.loads(row["tags"])
                    if isinstance(loaded, list):
                        tags = [str(t) for t in loaded]
                except json.JSONDecodeError:
                    pass
            peer_id = row["id"]
            peers.append((
                peer_id,
                DraftItem(
                    title=row["title"] or "",
                    content=row["content"] or "",
                    content_type=row["content_type"] or "convention",
                    source_url=self._peer_source_url(peer_id, row["source_url"], url_counts),
                    tags=tags,
                    signal="",
                ),
            ))
        return peers

    async def _persist_incoherent_vs_actives(
        self, project_id: str, new_id: str, draft: Dict[str, Any],
    ) -> None:
        peer_rows = await self._load_active_peers(project_id)
        if not peer_rows:
            return
        new_draft = DraftItem(
            title=draft["title"],
            content=draft["content"],
            content_type=draft.get("content_type", "convention"),
            source_url="auto-extract",
            tags=list(draft.get("tags") or []),
            signal="",
        )
        peers = [item for _, item in peer_rows]
        result = gate_drafts([new_draft], [], Path("."), peers=peers)
        detector = ConflictDetector(self._db)
        for conflict in result.conflicts:
            if conflict.conflict_type != "incoherent":
                continue
            hist_id = next(
                (
                    pid for pid, item in peer_rows
                    if item.source_url.replace("\\", "/") == conflict.right_source
                ),
                None,
            )
            if hist_id:
                conflict.reason = f"{conflict.reason} historical_id={hist_id}"
            conflict.left_source = "auto-extract"
            await detector.persist_knowledge_conflicts(
                project_id, [conflict], {"auto-extract": new_id},
            )

    async def cleanup_rejected(self) -> int:
        """rejected 条目留存 30 天后删除（§4.3）"""
        conn = await self._db.connect()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=_REJECTED_RETENTION_DAYS)).isoformat()
        cur = await conn.execute(
            "DELETE FROM knowledge_items WHERE status = 'rejected' AND updated_at < ?", (cutoff,)
        )
        await conn.commit()
        return cur.rowcount
