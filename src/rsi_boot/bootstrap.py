"""组装运行时（v3.0：零 Key 主链路）：配置 → MemoryStore → 召回/注入/学习闭环。

主链路不装配任何 LLM 组件：生成全部透传宿主模型。v2.6 模型调用层（Pipeline /
ModelAdapter / 质量评判 / 回放门禁）归档为附录 C 可选增强——仅当 enhance.* 对应
开关启用时装配（Spec v3.0 §10）。

配置热加载（P1.14）：Runtime 持有 ConfigWatcher；reload 时以新配置重建
检索/召回/学习组件。MemoryStore 与日志服务复用。热路径不构造 SQLite。
"""

from __future__ import annotations

import copy
import logging
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any, Optional

from .config.loader import ConfigWatcher
from .core import masking
from .feedback.implicit_tracker import FeedbackWorker
from .injector.conflict import ConflictDetector
from .injector.rule_injector import RuleInjector
from .learning.knowledge_extractor import KnowledgeExtractor
from .learning.proposal_engine import ProposalEngine
from .learning.skill_loader import SkillLoader
from .learning.snapshot_store import SnapshotStore
from .learning.static_gate import StaticGate
from .memory.store import MemoryStore
from .project import (
    load_or_create_identity,
    project_db_path,
    resolve_project_root,
    rsi_home,
)
from .rag.index import build_index
from .services.decision_queue import DecisionQueue
from .services.knowledge_service import KnowledgeService
from .services.log_service import LogService
from .services.profile_service import ProfileService
from .services.recall_service import RecallService
from .strategy.recall import RecallArmSelector

logger = logging.getLogger(__name__)


class _IndexInvalidator:
    """File-runtime stand-in for KnowledgeRetriever.invalidate_cache."""

    def __init__(self, invalidate) -> None:
        self._invalidate = invalidate

    def invalidate_cache(self) -> None:
        self._invalidate()

#: 增强层开关 → 是否需要 LLM 适配器（附录 C）
_LLM_SWITCHES = ("extract_llm", "proposal_llm", "gate_replay", "quality_judge", "conflict_llm")


def _enhance_view(config: dict[str, Any]) -> dict[str, Any]:
    """增强层组件的配置视图：enhance.<section> 合并回 v2.6 组件期望的顶层键
    （如 enhance.model.* → model.*），使归档组件不改代码即可复用"""
    view = copy.deepcopy(config)
    enhance = config.get("enhance", {})
    for section in ("model", "knowledge", "harness", "intent", "quality", "gate"):
        override = enhance.get(section)
        if isinstance(override, dict):
            base = view.get(section) if isinstance(view.get(section), dict) else {}
            view[section] = {**base, **override}
    return view


class Runtime:
    def __init__(
        self,
        watcher: ConfigWatcher,
        store: MemoryStore,
        project_root: Optional[Path] = None,
        project_id: Optional[str] = None,
    ):
        self.watcher = watcher
        self.store = store
        self.project_id = project_id
        self.logs = LogService(store=store)
        self.profiles = ProfileService(store=store)
        self.feedback_worker = FeedbackWorker(store=store, profiles=self.profiles)
        self.snapshots = SnapshotStore(store=store, rsi_home=rsi_home())
        self._project_root = project_root
        self.vec = None
        self.decisions = DecisionQueue()
        self._index: Optional[dict[str, Any]] = None
        from .memory.graph import prune_catalog

        prune_catalog(store.rsi_dir, store)
        self._apply_config(watcher.config)
        watcher.subscribe(self._apply_config)

    @property
    def config(self) -> dict[str, Any]:
        return self.watcher.config

    def invalidate_index(self) -> None:
        self._index = None

    def invert_index(self) -> dict[str, Any]:
        if self._index is None:
            import json

            docs = [
                d for d in self.store.list_official(skip_harvest=True) if d.status == "active"
            ]
            self._index = build_index(docs)
            cache = self.store.rsi_dir / "cache"
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "inverted.json").write_text(
                json.dumps(self._index, ensure_ascii=False),
                encoding="utf-8",
            )
        return self._index

    def _apply_config(self, config: dict[str, Any]) -> None:
        """配置生效点：脱敏规则 + 依赖配置的组件全部重建（热加载回调）"""
        masking.configure(config)
        enhance = config.get("enhance", {})
        view = _enhance_view(config)

        embedding = None
        if enhance.get("embedding"):
            from .knowledge.embedding import EmbeddingService

            embedding = EmbeddingService(view)
        adapter = None
        if any(enhance.get(k) for k in _LLM_SWITCHES):
            from .model.adapter import ModelAdapter
            from .model.registry import ModelRegistry

            adapter = ModelAdapter(ModelRegistry(view), view)
        quality = None
        if enhance.get("quality_judge") and adapter is not None and embedding is not None:
            from .quality.assessor import QualityAssessor

            quality = QualityAssessor(embedding, adapter, view)

        self.retriever = _IndexInvalidator(self.invalidate_index)
        self.injector = RuleInjector(store=self.store, project_root=self._project_root)
        self.knowledge = KnowledgeService(
            store=self.store,
            bound_project_id=self.project_id,
            project_root=self._project_root,
        )

        async def _on_knowledge_change(project_id: str) -> None:
            self.invalidate_index()
            await self.injector.rewrite(project_id)

        self.knowledge.on_change = _on_knowledge_change
        self.recall_arms = RecallArmSelector(store=self.store)
        self.recall = RecallService(
            store=self.store,
            feedback_secret=self.feedback_secret,
            profiles=self.profiles,
            bound_project_id=self.project_id,
            decisions=self.decisions,
            project_root=self._project_root,
        )
        self.extractor = KnowledgeExtractor(
            store=self.store, embedding=embedding, adapter=adapter, config=config,
        )
        gate: Any = StaticGate()
        if enhance.get("gate_replay"):
            logger.warning("enhance.gate_replay 已开启但 Runtime 不再装配 sqlite 回放门禁，回落静态门禁")
        self.proposal_engine = ProposalEngine(
            store=self.store, adapter=adapter, config=config, gate=gate,
            snapshots=self.snapshots, rsi_home=rsi_home(),
            on_change=self.injector.rewrite,
        )
        self.conflict_detector = ConflictDetector(
            store=self.store, project_root=self._project_root,
            on_change=self.injector.rewrite,
            knowledge=self.knowledge,
        )
        skill_dirs = [Path(str(resources.files("rsi_boot") / "config" / "skills")), rsi_home() / "skills"]
        if self._project_root:
            skill_dirs.append(Path(self._project_root) / ".rsi" / "skills")
        self.skill_loader = SkillLoader(skill_dirs)
        self.feedback_worker._quality = quality
        self.invalidate_index()

    @property
    def feedback_secret(self) -> str:
        return self.config.get("feedback", {}).get("secret_key") or "local-dev-secret"

    async def close(self) -> None:
        try:
            await self.recall.drain()
        except Exception:
            pass
        await self.feedback_worker.stop()


def default_db_path(project_root: Optional[Path] = None) -> Path:
    """当前项目库路径（仅 migrate 命令使用）。无参时按 cwd 解析项目根。"""
    root = resolve_project_root(explicit=project_root)
    return project_db_path(root)


async def build_runtime(db_path: Optional[Path] = None, project_root: Optional[Path] = None) -> Runtime:
    del db_path  # file runtime ignores sqlite path
    root = Path(project_root).resolve() if project_root is not None else resolve_project_root()
    identity = load_or_create_identity(root)
    project_id = identity.project_id
    rsi_dir = root / ".rsi"
    rsi_dir.mkdir(parents=True, exist_ok=True)
    watcher = ConfigWatcher(project_root=root)
    store = MemoryStore(rsi_dir)
    return Runtime(watcher, store, project_root=root, project_id=project_id)


def detect_user_id() -> str:
    """§7.2：user_id 取 git config user.email，缺省 local"""
    try:
        out = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True, text=True, timeout=5,
        )
        email = out.stdout.strip()
        if email:
            return email
    except (OSError, subprocess.SubprocessError):
        pass
    return "local"
