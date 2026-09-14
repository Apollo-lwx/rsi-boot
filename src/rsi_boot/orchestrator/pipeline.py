"""Pipeline 编排（§4.1）：Preprocess → Strategy → Model → Postprocess → Log。

日志两段式写入：Preprocess 生成 feedback_token 后，Model 调用前先 INSERT pending，
响应完成后 UPDATE 终态（§4.1）。预算检查在 Strategy 之前（§6.2）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, List, Optional

from cachetools import TTLCache

from ..core.exceptions import ModelProviderError
from ..core.masking import mask_messages
from ..core.models import (
    ContentItem,
    ContentType,
    CostInfo,
    KnowledgeItem,
    ProcessedRequest,
    RSIRequest,
    RSIResponse,
    generate_feedback_token,
)
from pathlib import Path

from ..data.sqlite import SQLiteClient
from ..knowledge.retriever import KnowledgeRetriever
from ..learning.skill_loader import SkillLoader
from ..memory.store import MemoryStore
from ..model.adapter import ModelAdapter
from ..model.providers.base import ModelResult
from ..preprocessor.intent_classifier import detect_intent_llm
from ..quality.assessor import QualityAssessor
from ..services.log_service import LogService
from ..services.profile_service import ProfileService
from ..strategy.engine import StrategyEngine
from ..strategy.prompt_renderer import PromptRenderer

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        db: SQLiteClient,
        config: dict[str, Any],
        retriever: KnowledgeRetriever,
        adapter: ModelAdapter,
        profiles: Optional[ProfileService] = None,
        quality: Optional[QualityAssessor] = None,
        skill_loader: Optional[SkillLoader] = None,
        store: MemoryStore | None = None,
    ):
        self._db = db
        self._config = config
        self._retriever = retriever
        self._adapter = adapter
        self._profiles = profiles
        self._quality = quality
        self._skills = skill_loader
        self._strategy = StrategyEngine(db, config)
        self._renderer = PromptRenderer()
        log_store = store or MemoryStore(Path(db.db_path).parent / ".rsi")
        self._logs = LogService(log_store)
        # 响应缓存（§2.4：maxsize=500, ttl=300s；默认关闭——个人工具重复提问少，
        # 开启后相同请求特征直接复用响应文本，命中行零 token 落库不污染用量统计）
        rc_cfg = config.get("response_cache", {})
        self._response_cache: Optional[TTLCache] = (
            TTLCache(maxsize=int(rc_cfg.get("maxsize", 500)), ttl=float(rc_cfg.get("ttl_s", 300)))
            if rc_cfg.get("enabled")
            else None
        )
        self._bg_tasks: set[asyncio.Task] = set()  # 画像增量等后台任务，close 前 drain

    async def run(self, request: RSIRequest) -> RSIResponse:
        project_id = request.project_id or "default"
        secret = self._config.get("feedback", {}).get("secret_key") or "local-dev-secret"
        feedback_token = generate_feedback_token(request.request_id, request.user_id, secret)

        # ---- Preprocess：意图识别（P2.2 LLM 分类，规则兜底）+ 混合知识检索 ----
        intent, confidence = (
            (request.intent, 1.0)
            if request.intent
            else await detect_intent_llm(request.raw_input, role=request.role, adapter=self._adapter, config=self._config)
        )
        retrieved = await self._retriever.search(request.raw_input, project_id, role=request.role)
        # 技能槽（§3.9）：trigger 命中的技能指令随知识一并注入，标注 skill:<name>，共用 §3.2 预算
        retrieved = self._inject_skills(intent, request.raw_input, retrieved)
        # 画像：读取合并（全局 + 项目）注入渲染上下文；运行时增量后台记录（§3.8）
        profile = await self._profiles.get(request.user_id, project_id) if self._profiles else None
        processed = ProcessedRequest(
            request=request, intent=intent, confidence=confidence,
            retrieved_docs=retrieved, merged_config=self._config, user_profile=profile,
        )
        if self._profiles is not None:
            self._spawn_profile_updates(request.user_id, project_id, intent, retrieved)

        # ---- 预算检查（策略选择之前，§6.2）：超限返回降级提示，force=true 覆盖 ----
        budget_warning = await self._check_budget()
        if budget_warning and budget_warning.startswith("EXCEEDED") and not request.preferences.force:
            return RSIResponse(
                request_id=request.request_id,
                status="partial",
                content=[ContentItem(type=ContentType.TEXT, body=budget_warning.removeprefix("EXCEEDED: "))],
                feedback_token=feedback_token,
            )

        # ---- Log 第一段：pending INSERT（模型调用前） ----
        log_id = await self._logs.insert_pending(request, feedback_token)

        started = time.monotonic()
        try:
            # ---- Strategy：确定路由 ----
            decision = await self._strategy.decide(project_id, intent, role=request.role)

            # ---- Model：渲染 + 调用（外发前二次脱敏，§8.1 双扫描时机） ----
            messages = self._renderer.render(decision.template_ref, processed)
            cache_key = self._response_cache_key(request, project_id, intent, decision, retrieved)
            cached = self._response_cache.get(cache_key) if cache_key is not None else None
            if cached is not None:
                # 命中：复用响应文本，零 token（缓存未产生新用量，统计保持真实）
                result = ModelResult(text=cached[0], model=cached[1], extra={"cache_hit": True})
                logger.info("响应缓存命中（model=%s）", cached[1])
            else:
                result = await self._adapter.complete(decision.model_name, mask_messages(messages))
                if cache_key is not None:
                    self._response_cache[cache_key] = (result.text, result.model)
            cost = self._adapter.calc_cost(result)
            latency_ms = int((time.monotonic() - started) * 1000)

            # ---- 质量评估（§3.5）：relevance/actionability 每次必算，accuracy 抽样 ----
            quality_score: Optional[float] = None
            if self._quality is not None:
                try:
                    qresult = await self._quality.assess(
                        request.raw_input, result.text, intent, request.role,
                        retrieved_docs=retrieved, feedback_token=feedback_token,
                    )
                    quality_score = qresult.quality_score
                except Exception:
                    logger.exception("质量评估失败（不阻塞响应）")

            # ---- Log 第二段：终态 UPDATE ----
            await self._logs.finalize(
                log_id, status="success", intent=intent, intent_confidence=confidence,
                strategy_name=decision.strategy_name, model_name=result.model,
                prompt_tokens=cost.prompt_tokens, completion_tokens=cost.completion_tokens,
                cost_usd=cost.cost_usd if self._adapter.is_priced(result.model) else None,
                latency_ms=latency_ms, quality_score=quality_score,
                response_excerpt=result.text,
                retrieved_tags=[t for d in retrieved for t in ([d.domain] if d.domain else []) + list(d.tags)],
            )

            # ---- Postprocess：构建响应 ----
            content = [ContentItem(type=ContentType.MARKDOWN, body=result.text[:65536])]
            response = RSIResponse(
                request_id=request.request_id,
                status="success",
                content=content,
                cost_info=cost,
                quality_score=quality_score,
                feedback_token=feedback_token,
            )
            markers: dict[str, Any] = {}
            if budget_warning:
                markers["budget_warning"] = budget_warning
            if cached is not None:
                markers["cache_hit"] = True
            if markers:
                response.error = None
                response.content[0].structured_data = markers
            return response

        except ModelProviderError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            await self._logs.finalize(
                log_id, status="error", intent=intent, intent_confidence=confidence,
                latency_ms=latency_ms,
            )
            return RSIResponse(
                request_id=request.request_id,
                status="error",
                content=[ContentItem(type=ContentType.ERROR, body=str(exc))],
                feedback_token=feedback_token,
                error={"type": type(exc).__name__, "message": str(exc)},
            )

    def _response_cache_key(
        self,
        request: RSIRequest,
        project_id: str,
        intent: str,
        decision: Any,
        retrieved: List[Any],
    ) -> Optional[str]:
        """请求特征 SHA-256（§2.4）：含模型/模板/命中知识，知识库或策略变更自然换 key"""
        if self._response_cache is None:
            return None
        doc_ids = ",".join(sorted(str(d.id) for d in retrieved if getattr(d, "id", None)))
        material = "|".join([
            request.user_id, project_id, request.role or "", intent,
            decision.model_name, decision.template_ref, request.raw_input, doc_ids,
        ])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _inject_skills(self, intent: str, raw_input: str, retrieved: List[Any]) -> List[Any]:
        """命中技能转为伪知识条目（title=skill:<name>）置顶注入；与检索结果共用 token 预算，
        超出预算时从检索结果末尾裁剪（技能优先级高于知识条目——程序性记忆指导行为）"""
        if self._skills is None:
            return retrieved
        matched = self._skills.match(intent, raw_input)
        if not matched:
            return retrieved
        skill_items = [
            KnowledgeItem(project_id="", title=f"skill:{s.name}", content=s.body, content_type="skill")
            for s in matched
        ]
        budget_chars = int(self._config.get("retrieval", {}).get("token_budget", 2000)) * 4
        used = sum(len(i.content) for i in skill_items)
        kept: List[Any] = []
        for item in retrieved:
            if used + len(item.content) > budget_chars:
                continue
            used += len(item.content)
            kept.append(item)
        if matched:
            logger.info("技能命中：%s", ", ".join(s.name for s in matched))
        return skill_items + kept

    def _spawn_profile_updates(
        self, user_id: str, project_id: str, intent: str, retrieved: List[Any]
    ) -> None:
        """画像运行时增量：后台任务执行，不阻塞主链路；失败仅记日志"""

        async def _update() -> None:
            assert self._profiles is not None
            await self._profiles.record_intent(user_id, project_id, intent)
            await self._profiles.record_retrieval_hits(user_id, project_id, retrieved)

        def _on_done(t: asyncio.Task) -> None:
            self._bg_tasks.discard(t)
            if not t.cancelled() and t.exception():
                logger.warning("画像增量更新失败: %s", t.exception())

        task = asyncio.create_task(_update())
        self._bg_tasks.add(task)
        task.add_done_callback(_on_done)

    async def drain(self, timeout: float = 2.0) -> None:
        """等待后台画像任务完成（Runtime.close 在关库前调用，防写已关闭连接）"""
        pending = [t for t in self._bg_tasks if not t.done()]
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout)
            except asyncio.TimeoutError:
                logger.warning("画像后台任务 drain 超时（%d 个未完成）", len(pending))

    async def _check_budget(self) -> Optional[str]:
        """每日 token 上限（默认关闭）；达 80% 告警，超限返回 EXCEEDED 前缀提示（§6.2）"""
        cap = int(self._config.get("budget", {}).get("daily_token_cap", 0) or 0)
        if cap <= 0:
            return None
        used = await self._logs.daily_token_usage()
        if used >= cap:
            return f"EXCEEDED: 当日 token 用量 {used} 已达上限 {cap}；可带 preferences.force=true 覆盖"
        if used >= cap * 0.8:
            return f"当日 token 用量 {used}/{cap}（已达 80%）"
        return None
