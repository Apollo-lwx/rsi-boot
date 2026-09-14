"""知识提取（Spec v3.0 §4.4）：候选汇集 → 规则式提取（默认，零 Key）→ 标题去重 → 人工确认队列。

流程：
1. 候选汇集：modified diff>20% 与 rejected+comment（反馈时由 FeedbackWorker 写入）+
   rating ≥ 4 的高质量交互（每日任务从 events.jsonl 汇集，按 source_event_id 幂等）
2. 规则式提取（默认）：modified→convention（修改稿沉淀）；rejected+comment→prohibition
   （comment 前缀规范化「禁止：」）；rating≥4→convention（问答对直接沉淀）
3. 去重：标题完全匹配 → 合并（刷新 updated_at）；无语义去重（embedding 属增强层）
4. 人工确认：草稿写入 pending YAML（不入检索），
   经 rsi_knowledge_review 确认后转 official；拒绝转 archive

LLM 提取与向量去重为附录 C 可选增强（enhance.extract_llm: true 时启用）。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..knowledge.embedding import EmbeddingService
from ..memory.logstore import iter_events
from ..memory.paths import pending_dir
from ..memory.store import MemoryStore, memory_filename
from ..memory.types import MemoryDoc
from .pipeline import extract_draft_rule as extract_draft_rule_fn
from .pipeline import extract_from_feedback as extract_from_feedback_fn

logger = logging.getLogger(__name__)

_EXTRACT_MODEL_DEFAULT = "gpt-4o-mini"
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
        store: MemoryStore,
        embedding: Optional[EmbeddingService] = None,
        adapter: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self._store = store
        self._embedding = embedding
        self._adapter = adapter
        self._rsi_dir = Path(store.rsi_dir)
        config = config or {}
        enhance = config.get("enhance", {})
        # 零 Key 默认：规则式提取；LLM 提取需 enhance.extract_llm + adapter 可用
        self._use_llm = bool(enhance.get("extract_llm")) and adapter is not None
        self._model = str(enhance.get("model", {}).get("default", _EXTRACT_MODEL_DEFAULT))

    def _store_or_dir(self) -> MemoryStore:
        return self._store

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
        """委托 pipeline 只写 pending YAML。"""
        return extract_from_feedback_fn(
            self._rsi_dir,
            action=action,
            comment=comment,
            retrieved=retrieved,
            **kwargs,
        )

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

    # ---- 4. 每日任务主流程 ----

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
        """汇集 → 提取（规则式默认）→ pending YAML；返回各环节计数"""
        stats = await self._run_daily_store()
        if any(stats.values()):
            logger.info("知识提取每日任务完成：%s", stats)
        return stats
