"""记忆召回服务（Spec v3.0 §4.3）：rsi_recall 的内核。

召回排序：禁止项命中一律置顶（不受分数门槛限制）→ 其余走检索管线。
每次召回落一行 events.jsonl（kind='recall'），feedback_token
照常签发——反馈闭环据此驱动召回臂学习与采纳率统计。
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.models import RSIRequest, generate_feedback_token
from ..memory.harvest import is_harvest_doc
from ..memory.store import MemoryStore
from ..memory.types import MemoryDoc
from ..strategy.recall import RecallArmSelector
from .decision_queue import DecisionQueue, collect_decision_cards
from .profile_service import HALF_LIFE_DAYS, ProfileService

logger = logging.getLogger(__name__)

_RECALL_SCAN_LIMIT = 200  # 禁止项匹配的项目内扫描上限
_STORE_DEFAULT_ARM = "recall-balanced"
_STORE_DEFAULT_TOP_N = 5
_HEALTH_SCORE_PENALTY = 0.5
_FEEDBACK_WEIGHT = 0.15
_FEEDBACK_PRIOR = 2.0  # Beta(1, 1)：票少时信号收回 0，乘数收回 1
_FEEDBACK_POS = frozenset({"accepted", "applied", "copied", "referenced"})
_FEEDBACK_NEG = frozenset({"rejected", "modified"})
_ITEM_TYPES = frozenset({"convention", "documentation"})
_SEARCH_TYPES = frozenset({
    "prohibition", "convention", "documentation",
    "gene_case", "teaching_case", "episode",
})
_CASE_ORDER = ("prohibition", "convention", "documentation", "teaching_case", "gene_case", "episode")
_PEER_SCORE_RATIO = 0.2

# 向量融合（RSI_ENABLE=vector，实验特性，默认关）：
# 门禁后词面幸存者与语义命中按 RRF（k=60）融合重排，
# 词面漏召但 cos ≥ _VECT_MIN_COS 的语义命中凭向量位次入场竞争。
_VECT_MIN_COS = 0.55
# 多跳级联（RSI_ENABLE=multihop，实验特性，默认关）：
# 首跳头部条目的高 IDF 桥接词发起第二跳，第二跳证据按 50% 折算入场。
_MULTIHOP_BRIDGE_TERMS = 6
_MULTIHOP_SEED_DOCS = 3
_MULTIHOP_DISCOUNT = 0.5


def _vote(action: object, rating: object) -> int:
    name = str(action or "")
    try:
        score = int(rating) if rating is not None and rating != "" else None
    except (TypeError, ValueError):
        score = None
    if name in _FEEDBACK_POS or (score is not None and score >= 4):
        return 1
    if name in _FEEDBACK_NEG or (score is not None and score <= 2):
        return -1
    return 0


def _feedback_weight(ts: object, now: datetime) -> float:
    """与画像同一半衰期。没有时间戳或解析失败时按刚刚发生。"""
    days = 0.0
    if ts:
        try:
            stamp = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            stamp = None
        if stamp is not None:
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            days = (now - stamp.astimezone(timezone.utc)).total_seconds() / 86400.0
            if days < 0:
                days = 0.0
    return 0.5 ** (days / HALF_LIFE_DAYS)


def _evidence_log(
    doc_id: str,
    health_index: Dict[str, Dict[str, Any]],
    signals: Dict[str, float],
) -> float:
    """反馈和健康度在对数空间里的增量。长度惩罚和标题权重已经在 BM25 里。"""
    extra = 0.0
    finding = health_index.get(doc_id)
    if finding and finding.get("status") in ("open", "awaiting_edit"):
        extra += math.log(_HEALTH_SCORE_PENALTY)
    signal = signals.get(doc_id)
    if signal is not None:
        extra += math.log(1.0 + _FEEDBACK_WEIGHT * signal)
    return extra


def _log_linear(lexical: float, extra: float) -> float:
    """BM25 加上反馈、健康度。lexical <= 0 时没有可用的对数分。"""
    if lexical <= 0:
        return float("-inf")
    return math.log(lexical) + extra


def _gate_margin(arm_threshold: float | None) -> float:
    """通道地板是 BM25 上的似然比。臂门槛按归一化分数标定，0.6 为中性，只微调这条线。
    RSI_PEER_RATIO 环境变量仅供基准消融（多跳第二证据常被地板切掉）。"""
    import os

    base_ratio = _PEER_SCORE_RATIO
    raw = os.environ.get("RSI_PEER_RATIO")
    if raw:
        try:
            base_ratio = max(float(raw), 1e-6)
        except ValueError:
            pass
    base = math.log(base_ratio)
    if arm_threshold is None or arm_threshold <= 0:
        return base
    return base + math.log(arm_threshold / 0.6)


def _rerank(
    ranked: list[tuple[str, float]],
    health_index: Dict[str, Dict[str, Any]],
    signals: Dict[str, float],
) -> list[tuple[str, float]]:
    """把词面分收成对数线性相关分后再排序。"""
    if not ranked:
        return ranked
    adjusted = [
        (doc_id, _log_linear(score, _evidence_log(doc_id, health_index, signals)))
        for doc_id, score in ranked
    ]
    adjusted.sort(key=lambda item: item[1], reverse=True)
    return adjusted


def _apply_log_gate(
    ranked: list[tuple[str, float]],
    best: float,
    margin: float,
    body_of: Dict[str, float],
    health_index: Dict[str, Dict[str, Any]],
    signals: Dict[str, float],
) -> list[tuple[str, float]]:
    """一次取舍。参照的是正文分，标题可以抬高自己，不能抬高别人的淘汰线。"""
    if not ranked or best == float("-inf"):
        return list(ranked)
    floor = best + margin
    kept: list[tuple[str, float]] = []
    for doc_id, score in ranked:
        body = _log_linear(
            float(body_of.get(doc_id, 0.0)),
            _evidence_log(doc_id, health_index, signals),
        )
        if score >= floor or body >= floor:
            kept.append((doc_id, score))
    return kept


def _fuse_vector(
    ranked: list[tuple[str, float]],
    vec_cos: Dict[str, float],
    doc_type: Dict[str, str],
    types: frozenset,
    floor: float,
    limit: int,
) -> list[tuple[str, float]]:
    """词面幸存者与语义命中按 RRF（k=60）融合重排（RSI_ENABLE=vector）。

    门禁已在词面证据上执行完毕，此阶段分数只用于排序与截断，故直接以
    RRF 位次分重排：双通道命中自然靠前，词面漏召但 cos ≥ _VECT_MIN_COS
    的语义命中凭向量位次入场竞争同一批槽位（截断到 limit，榜单对比公平）。
    返回的分数为 RRF 分（只保证单调，不与词面对数分同尺度）。
    """
    if not vec_cos:
        return ranked
    vec_ranked = sorted(
        (
            (doc_id, cos)
            for doc_id, cos in vec_cos.items()
            if cos >= _VECT_MIN_COS and doc_type.get(doc_id) in types
        ),
        key=lambda kv: kv[1],
        reverse=True,
    )
    if not vec_ranked:
        return ranked
    rrf: Dict[str, float] = {}
    for rank, (doc_id, _score) in enumerate(ranked):
        rrf[doc_id] = rrf.get(doc_id, 0.0) + 1.0 / (60 + rank + 1)
    for rank, (doc_id, _cos) in enumerate(vec_ranked):
        rrf[doc_id] = rrf.get(doc_id, 0.0) + 1.0 / (60 + rank + 1)
    merged = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)
    return merged[:limit]


def _bridge_terms(index: dict, query: str, seed_doc_ids: List[str]) -> List[str]:
    """多跳桥接词：首跳头部文档里的高 IDF 词，且不在原查询中（第二跳的接力棒）。"""
    from ..rag.index import _doc_terms, tokenize

    idf: Dict[str, float] = index.get("idf") or {}
    q_terms = set(tokenize(query))
    best: Dict[str, float] = {}
    for doc_id in seed_doc_ids:
        for term in _doc_terms(index, doc_id) - q_terms:
            weight = idf.get(term, 0.0)
            if len(term) >= 3 and weight > 0.0:
                best[term] = max(best.get(term, 0.0), weight)
    return [
        term for term, _ in sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    ][: _MULTIHOP_BRIDGE_TERMS]


def _merge_hop(
    ranked: list[tuple[str, float]],
    hop_ranked: list[tuple[str, float]],
    doc_type: Dict[str, str],
    types: frozenset,
    floor: float,
    best: float,
    limit: int,
) -> list[tuple[str, float]]:
    """第二跳证据入场：hop 分归一化后映射到 [floor, floor + span·折扣] 区间。

    不同查询的 BM25 分不可直接比较，故按 hop 内顶分归一；最强二跳证据落在
    地板与峰值的中间（折扣 0.5），足以挤进满员通道，但不会压过词面强证据。
    """
    if not hop_ranked or floor == float("-inf"):
        return ranked
    present = {doc_id for doc_id, _ in ranked}
    max_hop = max((score for _, score in hop_ranked), default=0.0)
    if max_hop <= 0:
        return ranked
    span = best - floor
    merged = list(ranked)
    for doc_id, hscore in hop_ranked:
        if doc_id in present or hscore <= 0:
            continue
        if doc_type.get(doc_id) not in types:
            continue
        ratio = hscore / max_hop
        merged.append((doc_id, floor + span * _MULTIHOP_DISCOUNT * ratio))
        present.add(doc_id)
    merged.sort(key=lambda item: item[1], reverse=True)
    return merged[:limit]


class RecallService:
    def __init__(
        self,
        store: MemoryStore,
        feedback_secret: str = "",
        profiles: Optional[ProfileService] = None,
        bound_project_id: Optional[str] = None,
        decisions: Optional[DecisionQueue] = None,
        project_root: Optional[Path] = None,
        arms: Optional[RecallArmSelector] = None,
    ):
        self._store = store
        self._arms = arms
        self._secret = feedback_secret
        self._profiles = profiles
        self.bound_project_id = bound_project_id
        self._decisions = decisions if decisions is not None else DecisionQueue()
        self._project_root = project_root
        self._bg_tasks: set[asyncio.Task] = set()  # 画像增量后台任务，close 前 drain
        self._index_key: Optional[str] = None
        self._index: Optional[dict] = None
        self._feedback_key: Optional[tuple] = None
        self._feedback_signals: Dict[str, float] = {}
        self._vector_index: Any = None  # rag.vector.VectorIndex，懒加载
        self._vector_tried: bool = False

    async def recall(
        self,
        task: str,
        project_id: str,
        user_id: str = "local",
        role: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await self._recall_from_store(task, project_id, user_id, role, top_k)

    async def _recall_from_store(
        self,
        task: str,
        project_id: str,
        user_id: str,
        role: Optional[str],
        top_k: Optional[int],
    ) -> Dict[str, Any]:
        from ..memory.logstore import append_event
        from ..project import bind_project_id
        from ..rag.ablation import active_ablations, is_enabled
        from ..rag.index import doc_hits_rare_term, rare_query_terms, search
        from ..rag.query import expand_query
        from ..rag.temporal import parse_query_temporal

        store = self._store
        project_id = bind_project_id(self.bound_project_id, project_id)
        started = time.monotonic()
        limit = min(top_k, 10) if top_k else _STORE_DEFAULT_TOP_N

        docs = self._store_search_docs(store, role)
        by_id = {doc.id: doc for doc in docs}
        expanded, intent, _confidence = expand_query(
            task, role=role, rsi_dir=store.rsi_dir,
        )
        index = self._index_for(docs)
        arm_name = _STORE_DEFAULT_ARM
        score_ratio: Optional[float] = None
        if self._arms is not None:
            arm = await self._arms.pick(project_id)
            arm_name = arm.name
            score_ratio = arm.threshold
            if top_k is None:
                limit = min(int(arm.top_n), 10)

        from ..rag.index import best_heading
        from ..ux.lang import detect_lang
        from ..ux.messages import t
        from ..learning.memory_health import attach_health, demote_for_health, recall_index

        health_index = recall_index(store.rsi_dir)
        signals = self._acceptance_signals(store)
        body_of: Dict[str, float] = {}

        project_root = self._project_root or store.rsi_dir.parent
        ablations = active_ablations(project_root)
        temporal_signal = None
        if "temporal" not in ablations:
            ref_raw = index.get("ref_date")
            ref = None
            if ref_raw:
                try:
                    from datetime import date as _date
                    ref = _date.fromisoformat(str(ref_raw))
                except ValueError:
                    ref = None
            temporal_signal = parse_query_temporal(task, ref)
        title_weight = 1.0 if "title" in ablations else None
        # 时间题引用块降权：引用的证据行日期属于原事件，引用记忆不应压过原件
        quote_weight = 1.0
        if (
            "quotediscount" not in ablations
            and temporal_signal is not None
            and getattr(temporal_signal, "is_temporal", False)
        ):
            from ..rag.index import TEMPORAL_QUOTE_WEIGHT
            quote_weight = TEMPORAL_QUOTE_WEIGHT

        def _channel(types: set[str], channel_limit: int, *, drop_weak: bool) -> list:
            pool = max(channel_limit * 4, channel_limit)
            kwargs: Dict[str, Any] = {}
            if drop_weak:
                # 比例门槛改在对数分上统一做，这里只留强词过滤。
                kwargs["min_score_ratio"] = 0.0
            if "weakfilter" in ablations:
                drop_weak = False
            ranked = search(
                index, expanded, types=types, top_n=pool, drop_weak=drop_weak,
                body_of=body_of, expansion_of=task,
                title_weight=title_weight, temporal_signal=temporal_signal,
                quote_weight=quote_weight,
                **kwargs,
            )
            ranked = _rerank(ranked, health_index, signals)
            if drop_weak and is_enabled("diversity", project_root):
                # 默认关：实测实体名主导词面重叠，误伤正当证据（见 benchmark/results/abl-mmr）
                from ..rag.index import diversify_ranked
                ranked = diversify_ranked(index, ranked)
            if drop_weak and is_enabled("coverage", project_root):
                from ..rag.index import coverage_rerank
                ranked = coverage_rerank(index, ranked, expanded)
            return ranked[:channel_limit]

        prohibition_ranked = _channel(
            {"prohibition"}, _RECALL_SCAN_LIMIT, drop_weak=False,
        )
        rare = rare_query_terms(index, expanded)
        if rare:
            prohibition_ranked = [
                row for row in prohibition_ranked
                if doc_hits_rare_term(index, row[0], rare)
            ]
        prohibitions = [
            by_id[doc_id]
            for doc_id, _score in prohibition_ranked
            if doc_id in by_id
        ]
        item_ranked = _channel(_ITEM_TYPES, limit, drop_weak=True)
        teach_ranked = _channel({"teaching_case"}, limit, drop_weak=True)
        gene_ranked = _channel({"gene_case"}, limit, drop_weak=True)
        episode_ranked = _channel({"episode"}, limit, drop_weak=True)
        # 淘汰线只看纯正文。健康度和反馈只改这条自己的分数。
        best_body = float("-inf")
        for ranked in (item_ranked, teach_ranked, gene_ranked):
            for doc_id, _score in ranked:
                best_body = max(
                    best_body,
                    _log_linear(float(body_of.get(doc_id, 0.0)), 0.0),
                )
        margin = _gate_margin(score_ratio)
        item_ranked = _apply_log_gate(
            item_ranked, best_body, margin, body_of, health_index, signals,
        )
        teach_ranked = _apply_log_gate(
            teach_ranked, best_body, margin, body_of, health_index, signals,
        )
        gene_ranked = _apply_log_gate(
            gene_ranked, best_body, margin, body_of, health_index, signals,
        )
        episode_ranked = _apply_log_gate(
            episode_ranked, best_body, margin, body_of, health_index, signals,
        )
        # 实验通道（默认关，RSI_ENABLE 或 rsi-boot.yaml `enable:` 打开）：
        # multihop——首跳桥接词发起第二跳级联（词面分数空间，须在向量融合之前）；
        # vector——词面与语义命中 RRF 融合（位次空间，放最后）
        floor = best_body + margin
        if is_enabled("multihop", project_root) and best_body != float("-inf"):
            seed = [
                doc_id
                for doc_id, _ in (item_ranked + teach_ranked + gene_ranked)
            ][: _MULTIHOP_SEED_DOCS]
            bridge = _bridge_terms(index, expanded, seed) if seed else []
            if bridge:
                doc_type = index.get("doc_type") or {}
                hop_ranked = search(
                    index, " ".join(bridge),
                    types=_ITEM_TYPES | {"teaching_case", "gene_case"},
                    top_n=limit * 2, drop_weak=True, title_weight=title_weight,
                )
                if hop_ranked:
                    item_ranked = _merge_hop(
                        item_ranked, hop_ranked, doc_type, _ITEM_TYPES,
                        floor, best_body, limit,
                    )
                    teach_ranked = _merge_hop(
                        teach_ranked, hop_ranked, doc_type, {"teaching_case"},
                        floor, best_body, limit,
                    )
                    gene_ranked = _merge_hop(
                        gene_ranked, hop_ranked, doc_type, {"gene_case"},
                        floor, best_body, limit,
                    )
        if is_enabled("vector", project_root):
            vec_cos = self._vector_cosines(store, docs, expanded)
            if vec_cos:
                doc_type = index.get("doc_type") or {}
                item_ranked = _fuse_vector(
                    item_ranked, vec_cos, doc_type, _ITEM_TYPES, floor, limit,
                )
                teach_ranked = _fuse_vector(
                    teach_ranked, vec_cos, doc_type, {"teaching_case"}, floor, limit,
                )
                gene_ranked = _fuse_vector(
                    gene_ranked, vec_cos, doc_type, {"gene_case"}, floor, limit,
                )
        items = [
            by_id[doc_id]
            for doc_id, _score in item_ranked
            if doc_id in by_id
        ]
        teaching = [
            by_id[doc_id]
            for doc_id, _score in teach_ranked
            if doc_id in by_id
        ]
        teach_ids = {doc.id for doc in teaching}
        genes = [
            by_id[doc_id]
            for doc_id, _score in gene_ranked
            if doc_id in by_id and doc_id not in teach_ids
        ]
        yaml_episodes = [
            by_id[doc_id]
            for doc_id, _score in episode_ranked
            if doc_id in by_id
        ]
        event_episodes = self._episodes_from_events(store, task, expanded)
        episodes = self._merge_episodes(yaml_episodes, event_episodes)
        skills = self._store_skill_catalog(store)
        prohibitions = demote_for_health(prohibitions, health_index)
        items = demote_for_health(items, health_index)
        teaching = demote_for_health(teaching, health_index)
        genes = demote_for_health(genes, health_index)
        hits = prohibitions + items + genes + teaching
        latency_ms = int((time.monotonic() - started) * 1000)

        request = RSIRequest(user_id=user_id, project_id=project_id, role=role, raw_input=task)
        token = generate_feedback_token(request.request_id, user_id, self._secret)
        retrieved = [doc.id for doc in hits]
        for row in episodes:
            eid = row.get("id")
            if isinstance(eid, str) and eid not in retrieved:
                retrieved.append(eid)
        event: Dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": "recall",
            "task": task,
            "retrieved": retrieved,
            "arm": arm_name,
            "token": token,
            "request_id": str(request.request_id),
            "user_id": user_id,
            "project_id": project_id,
            "intent": intent,
            "latency_ms": latency_ms,
        }
        if hits:
            event["excerpt"] = "\n".join(doc.title for doc in hits)
        append_event(store.rsi_dir, event)
        self._maybe_snapshot(project_id)

        cards = await collect_decision_cards(
            store, project_id, project_root=self._project_root,
        )
        picked = self._decisions.pick(cards)
        decisions: list[dict[str, Any]] = []
        if picked is not None:
            picked.more_waiting = self._decisions.remaining_after(cards, picked)
            self._decisions.mark_presented(picked.id)
            decisions = [picked.asdict()]

        teaching_rows = [
            attach_health(
                self._case_payload(doc, heading=best_heading(index, doc.id, expanded)),
                doc, health_index,
            )
            for doc in teaching
        ]
        gene_rows = [
            attach_health(
                self._case_payload(doc, heading=best_heading(index, doc.id, expanded)),
                doc, health_index,
            )
            for doc in genes
        ]
        return {
            "prohibitions": [
                attach_health(self._prohibition_payload(doc), doc, health_index)
                for doc in prohibitions
            ],
            "items": [
                attach_health(
                    self._item_payload(doc, heading=best_heading(index, doc.id, expanded)),
                    doc, health_index,
                )
                for doc in items
            ],
            "gene_cases": gene_rows,
            "teaching_cases": teaching_rows,
            "suggestions": self._suggestion_rows(teaching_rows, gene_rows),
            "episodes": episodes,
            "skills": skills,
            "hint": t("HINT_TEACH", detect_lang(task)),
            "feedback_token": token,
            "recall_arm": arm_name,
            "decisions": decisions,
            "retrieved": retrieved,
        }

    def _maybe_snapshot(self, project_id: Optional[str]) -> None:
        """机会式日快照：每天第一条召回顺带落一次日聚合（攒长期曲线，无常驻进程）。
        先廉价判当天是否已落过，落过了就直接返回，绝不为快照重复全库扫。fail-open。"""
        try:
            from ..metrics.snapshot import has_today
            if has_today(self._store.rsi_dir):
                return
            from .impact_service import ImpactService
            ImpactService(self._store, project_root=self._project_root).snapshot_now(project_id)
        except Exception:  # 快照是旁路观测，任何异常都不得影响召回主链路
            pass

    def _vector_cosines(
        self, store: MemoryStore, docs: List[MemoryDoc], query: str
    ) -> Dict[str, float]:
        """向量通道（实验特性）：懒加载 VectorIndex，只试一次；
        未装 fastembed / 模型不可用 / 打分失败均返回空（fail-open，退回纯词面）"""
        if not self._vector_tried:
            self._vector_tried = True
            from ..rag.vector import VectorIndex

            self._vector_index = VectorIndex.create(store.rsi_dir)
        if self._vector_index is None:
            return {}
        return self._vector_index.query(docs, query, top_k=_RECALL_SCAN_LIMIT)

    def _index_for(self, docs: List[MemoryDoc]) -> dict:
        """Rebuild the inverted index only when the active memory set changes."""
        from ..rag.index import build_index, index_stamp

        key = index_stamp(docs)
        if self._index is not None and self._index_key == key:
            return self._index
        self._index = build_index(docs)
        self._index_key = key
        return self._index

    def _acceptance_signals(self, store: MemoryStore) -> Dict[str, float]:
        """单条记忆的反馈，范围 (-1, 1)。

        每票按画像半衰期贴现。后验是 Beta(1+采纳, 1+否定) 的中心化均值，
        票少时收回 0，排序乘数因此靠近 1。
        """
        from ..memory.logstore import iter_events

        now = datetime.now(timezone.utc)
        logs = store.rsi_dir / "logs"
        try:
            files = [path for path in logs.glob("events*.jsonl") if path.is_file()]
        except OSError:
            files = []
        file_key = tuple(
            (path.name, path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(files)
        )
        key = (now.date().isoformat(), file_key)
        if self._feedback_key == key:
            return self._feedback_signals
        retrieved_by_token: Dict[str, List[str]] = {}
        feedbacks: List[Dict[str, Any]] = []
        if files:
            for event in iter_events(store.rsi_dir, all_rolls=True):
                token = event.get("token")
                if not token:
                    continue
                if event.get("kind") == "recall":
                    retrieved_by_token[str(token)] = [
                        str(item) for item in (event.get("retrieved") or [])
                    ]
                elif event.get("kind") == "feedback":
                    feedbacks.append(event)
        positive: Dict[str, float] = {}
        negative: Dict[str, float] = {}
        for event in feedbacks:
            vote = _vote(event.get("action"), event.get("rating"))
            if vote == 0:
                continue
            weight = _feedback_weight(event.get("ts"), now)
            for doc_id in retrieved_by_token.get(str(event.get("token")), []):
                bucket = positive if vote > 0 else negative
                bucket[doc_id] = bucket.get(doc_id, 0.0) + weight
        signals: Dict[str, float] = {}
        for doc_id in set(positive) | set(negative):
            up = positive.get(doc_id, 0.0)
            down = negative.get(doc_id, 0.0)
            total = up + down
            if total <= 0:
                continue
            signals[doc_id] = (up - down) / (_FEEDBACK_PRIOR + total)
        self._feedback_key = key
        self._feedback_signals = signals
        return signals

    def _store_search_docs(self, store: MemoryStore, role: Optional[str]) -> List[MemoryDoc]:
        docs: List[MemoryDoc] = []
        teaching_ids: set[str] = set()
        for typ in _CASE_ORDER:
            for doc in store.list_official(typ, skip_harvest=True):
                if doc.status != "active" or doc.type not in _SEARCH_TYPES:
                    continue
                if doc.type in _ITEM_TYPES or doc.type == "prohibition":
                    if is_harvest_doc(doc):
                        continue
                if doc.roles and role and role not in doc.roles:
                    continue
                if typ == "teaching_case":
                    teaching_ids.add(doc.id)
                if typ == "gene_case" and doc.id in teaching_ids:
                    continue
                docs.append(doc)
        return docs

    def _store_skill_catalog(self, store: MemoryStore) -> List[Dict[str, Any]]:
        catalog: List[Dict[str, Any]] = []
        for doc in store.list_official("skill"):
            if doc.status != "active":
                continue
            catalog.append({
                "name": str(doc.payload.get("name") or doc.title),
                "description": doc.description or "",
                "path": doc.path or "",
            })
        return catalog

    @staticmethod
    def _prohibition_payload(doc: MemoryDoc) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": doc.id,
            "title": doc.title,
            "content": doc.content,
            "domain": doc.domain,
            "source_path": doc.path,
        }
        condition = RecallService._condition(doc)
        if condition:
            payload["condition"] = condition
        return RecallService._apply_flags(payload, doc)

    @staticmethod
    def _condition(doc: MemoryDoc) -> Dict[str, Any]:
        """前提/失效条件提示：命中记忆附带条件时，宿主须先核验前提是否仍成立再遵守。"""
        cond: Dict[str, Any] = {}
        if doc.trigger_condition:
            cond["must_hold"] = doc.trigger_condition
        if doc.expiry_condition:
            cond["may_expire_when"] = doc.expiry_condition
        return cond

    @staticmethod
    def _apply_flags(payload: Dict[str, Any], doc: MemoryDoc) -> Dict[str, Any]:
        """降置信标记：该条目曾被拒/低分反馈，宿主应存疑而非无条件遵守。"""
        from ..learning.governance import is_low_confidence

        if is_low_confidence(doc):
            payload["low_confidence"] = True
        return payload

    @staticmethod
    def _item_payload(doc: MemoryDoc, heading: str = "") -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": doc.id,
            "title": doc.title,
            "content": doc.content,
            "content_type": doc.type,
            "source_path": doc.path,
            "heading": heading,
        }
        condition = RecallService._condition(doc)
        if condition:
            payload["condition"] = condition
        extra = doc.extra or {}
        nested = doc.payload or {}
        for key in ("weight", "conflict_open"):
            if key in extra:
                payload[key] = extra[key]
            elif key in nested:
                payload[key] = nested[key]
        return RecallService._apply_flags(payload, doc)

    @staticmethod
    def _case_weight_and_role(doc: MemoryDoc) -> tuple[int, str]:
        payload = doc.payload or {}
        lesson = payload.get("lesson") if isinstance(payload.get("lesson"), dict) else {}
        fix = payload.get("fix_and_learn") if isinstance(payload.get("fix_and_learn"), dict) else {}
        raw = payload.get("weight")
        if raw is None:
            raw = fix.get("gene_map_weight")
        try:
            weight = int(raw) if raw is not None else 2
        except (TypeError, ValueError):
            weight = 2
        author = str(lesson.get("author") or payload.get("author") or "")
        role = "constraint" if weight >= 8 or author == "user" else "suggestion"
        return weight, role

    @staticmethod
    def _case_payload(doc: MemoryDoc, heading: str = "") -> Dict[str, Any]:
        weight, role = RecallService._case_weight_and_role(doc)
        payload: Dict[str, Any] = {
            "id": doc.id,
            "title": doc.title,
            "content": doc.content,
            "source_path": doc.path,
            "heading": heading,
            "weight": weight,
            "role": role,
        }
        condition = RecallService._condition(doc)
        if condition:
            payload["condition"] = condition
        return RecallService._apply_flags(payload, doc)

    @staticmethod
    def _suggestion_rows(
        teaching_rows: List[Dict[str, Any]],
        gene_rows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for row in teaching_rows + gene_rows:
            rid = row.get("id")
            if row.get("role") != "suggestion" or not isinstance(rid, str) or rid in seen:
                continue
            seen.add(rid)
            out.append(row)
        return out

    def _episodes_from_events(
        self, store: MemoryStore, task: str, expanded: str,
    ) -> List[Dict[str, Any]]:
        from ..memory.logstore import iter_events
        from ..rag.index import tokenize
        from ..rag.query import expand_query

        def _fp_terms(text: str) -> set[str]:
            ev_exp, ev_intent, _ = expand_query(text, rsi_dir=store.rsi_dir)
            return set(tokenize(ev_exp)) - set(tokenize(ev_intent))

        q_terms = _fp_terms(task)
        if not q_terms:
            q_terms = set(tokenize(expanded))
        events = list(iter_events(store.rsi_dir))
        recalls = [
            ev for ev in events
            if ev.get("kind") == "recall" and ev.get("task") and ev.get("id")
        ]
        feedbacks = [ev for ev in events if ev.get("kind") == "feedback"]
        matched: List[Dict[str, Any]] = []
        for ev in recalls:
            ev_task = str(ev["task"])
            if _fp_terms(ev_task) & q_terms:
                matched.append(ev)
        if not matched:
            return []

        groups: Dict[str, List[Dict[str, Any]]] = {}
        for ev in matched:
            key = " ".join(sorted(_fp_terms(str(ev["task"]))))
            groups.setdefault(key, []).append(ev)

        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for key, group in groups.items():
            tokens = {ev.get("token") for ev in group if ev.get("token")}
            fbs = [fb for fb in feedbacks if fb.get("token") in tokens]
            should_write = (
                len(fbs) >= 2
                or any(int(fb.get("rating") or 0) >= 4 for fb in fbs)
                or any(fb.get("action") == "rejected" and fb.get("comment") for fb in fbs)
            )
            representative = group[0]
            eid = str(representative["id"])
            path = self._materialize_episode(store, representative, key) if should_write else ""
            if eid in seen:
                continue
            seen.add(eid)
            out.append({
                "id": eid,
                "title": str(representative.get("task") or "")[:120],
                "task": representative.get("task"),
                "content": representative.get("excerpt") or representative.get("task") or "",
                "source_path": path,
            })
        return out

    def _materialize_episode(
        self, store: MemoryStore, ev: Dict[str, Any], fingerprint: str,
    ) -> str:
        from ..memory.paths import official_dir
        from ..memory.store import memory_filename

        eid = str(ev["id"])
        try:
            existing = store.read(eid)
            if existing.type == "episode" and existing.path:
                return existing.path
        except FileNotFoundError:
            pass
        title = str(ev.get("task") or "episode")[:120]
        dest = official_dir(store.rsi_dir, "episode") / memory_filename(title, eid)
        written = store.write(
            MemoryDoc(
                id=eid,
                type="episode",
                title=title,
                content=str(ev.get("excerpt") or ev.get("task") or "")[:20000],
                payload={"fingerprint": fingerprint},
            ),
            dest=dest,
        )
        return written.path or ""

    @staticmethod
    def _merge_episodes(
        yaml_docs: List[MemoryDoc], event_rows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        by_id: Dict[str, Dict[str, Any]] = {}
        for doc in yaml_docs:
            by_id[doc.id] = {
                "id": doc.id,
                "title": doc.title,
                "content": doc.content,
                "source_path": doc.path,
                "task": doc.title,
            }
        for row in event_rows:
            eid = str(row.get("id") or "")
            if not eid:
                continue
            if eid in by_id:
                if row.get("source_path") and not by_id[eid].get("source_path"):
                    by_id[eid]["source_path"] = row["source_path"]
            else:
                by_id[eid] = row
        return list(by_id.values())

    async def drain(self, timeout: float = 2.0) -> None:
        """等待后台画像任务完成（Runtime.close 在关库前调用）"""
        pending = [t for t in self._bg_tasks if not t.done()]
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout)
            except asyncio.TimeoutError:
                logger.warning("画像后台任务 drain 超时（%d 个未完成）", len(pending))
        if self._vector_index is not None:
            self._vector_index.close()
            self._vector_index = None
