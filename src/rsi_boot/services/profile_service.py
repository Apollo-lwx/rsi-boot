"""用户画像服务（§3.8，P2.4）。

存储与缓存映射：
- 持久层 user_profiles.profile_data（JSON，主键 (user_id, project_id)，'' = 全局画像）
- 缓存层进程内 TTLCache（maxsize=50, ttl=600s，§2.4；原 Redis 形态的本地化）
- 读路径：缓存 miss → 读库 → 回填；全局画像与项目画像同时存在时项目字段覆盖全局
- 写路径：写穿——先 UPDATE 落库，再 DEL 缓存（禁止先删缓存后写库，防并发脏读）

运行时增量：record_intent / record_retrieval_hits（Feedback Worker 与管线调用）。
preferences.* 不从隐式行为自动改写（§3.8 约束：防噪声污染偏好）。
离线聚合 rebuild：近 90 天日志衰减重算（半衰期 30 天），由 §4.3 每日任务调用。
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from cachetools import TTLCache

from ..core.exceptions import CircuitOpenError
from ..core.models import KnowledgeItem, UserPreferences, UserProfile
from ..data.sqlite import SQLiteClient, is_operational_error

logger = logging.getLogger(__name__)

HALF_LIFE_DAYS = 30  # 行为计数指数衰减半衰期（§3.8）
_MIN_MODEL_EXPOSURE = 20  # 偏好模型统计最小曝光（防小样本噪声）
_MAX_INTERESTS = 20  # knowledge_interests Top 20


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decay(days_since: float) -> float:
    return 0.5 ** (days_since / HALF_LIFE_DAYS)


class ProfileService:
    def __init__(self, db: SQLiteClient, global_db: Optional[SQLiteClient] = None):
        self._db = db
        self._global_db = global_db or db
        self._cache: TTLCache = TTLCache(maxsize=50, ttl=600)

    def _store_for(self, project_id: Optional[str]) -> SQLiteClient:
        """project_id 为空 = 全局画像，落 global.db；否则落项目库"""
        if not project_id:
            return self._global_db
        return self._db

    @staticmethod
    def _key(user_id: str, project_id: Optional[str]) -> str:
        return f"{user_id}:{project_id or ''}"

    # ---------- 读 ----------

    async def get(self, user_id: str, project_id: Optional[str] = None) -> UserProfile:
        """缓存 miss → 读库（全局 + 项目合并）→ 回填；无记录返回默认画像（冷启动兜底）。
        读取失败（含 sqlite 熔断 OPEN）降级为默认画像，不阻塞主链路（§3.8/§5.4 Level 2）"""
        key = self._key(user_id, project_id)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        profiles: List[UserProfile] = []
        try:
            self._db.breaker.allow_request()
            global_row = await self._load_row(user_id, "")
            if global_row:
                profiles.append(UserProfile(**json.loads(global_row)))
            if project_id:
                project_row = await self._load_row(user_id, project_id)
                if project_row:
                    profiles.append(UserProfile(**json.loads(project_row)))
            self._db.breaker.on_success()
        except Exception as exc:
            if not isinstance(exc, CircuitOpenError):
                self._db.breaker.on_failure(countable=is_operational_error(exc))
            logger.error("画像读取失败（降级默认画像）: %s", exc)
            return UserProfile(user_id=user_id, project_id=project_id)

        if not profiles:
            profile = UserProfile(user_id=user_id, project_id=project_id)
        elif len(profiles) == 1:
            profile = profiles[0]
        else:
            profile = self._merge(global_p=profiles[0], project_p=profiles[1])
        self._cache[key] = profile
        return profile

    async def _load_row(self, user_id: str, project_id: str) -> Optional[str]:
        store = self._store_for(project_id)
        conn = await store.connect()
        async with conn.execute(
            "SELECT profile_data FROM user_profiles WHERE user_id = ? AND project_id = ?",
            (user_id, project_id),
        ) as cur:
            row = await cur.fetchone()
        return str(row["profile_data"]) if row else None

    @staticmethod
    def _merge(global_p: UserProfile, project_p: UserProfile) -> UserProfile:
        """项目画像字段覆盖全局画像（与 §2.5 配置合并方向一致）"""
        merged = project_p.model_copy(deep=True)
        merged.user_id = project_p.user_id
        # preferences：项目侧仍为模型默认值的字段回落全局（无法区分「显式设为默认值」与「未设置」，
        # 个人工具场景按默认值即未设置处理，与 §2.5 合并方向一致）
        defaults = UserPreferences().model_dump()
        g_prefs = global_p.preferences.model_dump()
        p_prefs = merged.preferences.model_dump()
        for field, value in g_prefs.items():
            if field == "role_specific":
                merged.preferences.role_specific = {**value, **p_prefs.get("role_specific", {})}
            elif p_prefs.get(field) == defaults.get(field) and value != defaults.get(field):
                setattr(merged.preferences, field, value)
        # 计数/列表类：项目优先，全局补充
        for intent, count in global_p.frequently_used_intents.items():
            merged.frequently_used_intents.setdefault(intent, count)
        merged.preferred_models = list(dict.fromkeys(project_p.preferred_models + global_p.preferred_models))
        merged.knowledge_interests = list(
            dict.fromkeys(project_p.knowledge_interests + global_p.knowledge_interests)
        )[:_MAX_INTERESTS]
        merged.expertise = list(dict.fromkeys(project_p.expertise + global_p.expertise))
        merged.project_insights = {**global_p.project_insights, **project_p.project_insights}
        if not merged.history_summary:
            merged.history_summary = global_p.history_summary
        return merged

    # ---------- 写（写穿：先落库再 DEL 缓存） ----------

    async def upsert(self, profile: UserProfile) -> None:
        profile.updated_at = datetime.now(timezone.utc)
        store = self._store_for(profile.project_id)
        conn = await store.connect()
        now = _utc_iso()
        await conn.execute(
            """
            INSERT INTO user_profiles (user_id, project_id, profile_data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, project_id) DO UPDATE SET profile_data = excluded.profile_data,
                                                           updated_at = excluded.updated_at
            """,
            (
                profile.user_id,
                profile.project_id or "",
                profile.model_dump_json(),
                now,
                now,
            ),
        )
        await conn.commit()
        self._cache.pop(self._key(profile.user_id, profile.project_id), None)

    # ---------- 运行时增量（§3.8 字段级规则） ----------

    async def record_intent(self, user_id: str, project_id: Optional[str], intent: str) -> None:
        """每次请求对应 intent 计数 +1（30 天滑动窗口由每日 rebuild 衰减重算）"""
        profile = await self.get(user_id, project_id)
        # 增量写到项目级画像（无项目时写全局）
        profile.frequently_used_intents[intent] = profile.frequently_used_intents.get(intent, 0) + 1
        await self.upsert(profile)

    async def record_retrieval_hits(
        self, user_id: str, project_id: Optional[str], items: List[KnowledgeItem]
    ) -> None:
        """检索命中文档的 domain/tags 计数 +1，保留 Top 20（超出淘汰计数最低项）"""
        if not items:
            return
        tags = [k for item in items for k in ([item.domain] if item.domain else []) + list(item.tags)]
        await self._bump_interests(user_id, project_id, tags)

    async def record_interest_boost(
        self, user_id: str, project_id: Optional[str], tags: List[str]
    ) -> None:
        """copied/referenced 反馈：该次检索命中标签再 +1（§4.2 映射表）"""
        await self._bump_interests(user_id, project_id, tags)

    async def _bump_interests(self, user_id: str, project_id: Optional[str], tags: List[str]) -> None:
        tags = [t for t in tags if t]
        if not tags:
            return
        profile = await self.get(user_id, project_id)
        counts: Dict[str, int] = dict(profile.project_insights.get("_interest_counts", {}))
        for k in tags:
            counts[k] = counts.get(k, 0) + 1
        top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:_MAX_INTERESTS]
        profile.project_insights["_interest_counts"] = dict(top)
        profile.knowledge_interests = [k for k, _ in top]
        await self.upsert(profile)

    # ---------- 离线聚合（§4.3 每日任务） ----------

    async def rebuild(self, user_id: str, project_id: Optional[str] = None) -> UserProfile:
        """近 90 天日志衰减重算：意图频率 / 偏好模型（曝光≥20）/ 知识兴趣 Top20"""
        conn = await self._db.connect()
        now = datetime.now(timezone.utc)
        # created_at 为 ISO8601 TEXT：截止时间在 Python 侧计算，同格式字典序可比
        cutoff = (now - timedelta(days=90)).isoformat()
        sql = """
            SELECT intent, model_name, feedback_action, created_at
            FROM interaction_logs
            WHERE user_id = ? AND status = 'success' AND created_at >= ?
        """
        params: list[Any] = [user_id, cutoff]
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        async with conn.execute(sql, params) as cur:
            logs = await cur.fetchall()

        profile = await self.get(user_id, project_id)

        # 1. 意图频率：衰减后重算
        intent_counts: Dict[str, float] = {}
        # 2. 偏好模型：曝光/采纳计数（采纳率 = (accepted + 0.5×modified) / 总反馈）
        model_exposure: Dict[str, int] = {}
        model_score: Dict[str, float] = {}
        for row in logs:
            created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            weight = _decay(max((now - created).total_seconds() / 86400, 0.0))

            if row["intent"]:
                intent_counts[row["intent"]] = intent_counts.get(row["intent"], 0.0) + weight
            model = row["model_name"]
            if model:
                model_exposure[model] = model_exposure.get(model, 0) + 1
                action = row["feedback_action"]
                if action in ("accepted", "applied"):
                    model_score[model] = model_score.get(model, 0.0) + weight
                elif action == "modified":
                    model_score[model] = model_score.get(model, 0.0) + 0.5 * weight

        profile.frequently_used_intents = {
            k: int(round(v)) for k, v in sorted(intent_counts.items(), key=lambda kv: -kv[1]) if v >= 0.5
        }
        profile.preferred_models = [
            m
            for m, _ in sorted(
                ((m, model_score.get(m, 0.0) / exp) for m, exp in model_exposure.items() if exp >= _MIN_MODEL_EXPOSURE),
                key=lambda kv: -kv[1],
            )
        ]

        # 3. 知识兴趣：对运行时计数施加衰减（逐条命中日志未落库，Phase 3 再细化）
        counts: Dict[str, int] = dict(profile.project_insights.get("_interest_counts", {}))
        if counts:
            decayed = {k: int(round(v * _decay(1.0))) for k, v in counts.items()}  # 每日任务：按 1 天衰减
            top = sorted(((k, v) for k, v in decayed.items() if v > 0), key=lambda kv: -kv[1])[:_MAX_INTERESTS]
            profile.project_insights["_interest_counts"] = dict(top)
            profile.knowledge_interests = [k for k, _ in top]

        # 4. preferences.* 保持现状（离线任务不猜测用户偏好，§3.8）
        await self.upsert(profile)
        return profile
