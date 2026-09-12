"""组装运行时（v3.0：零 Key 主链路）：配置 → SQLite → 检索 → 召回/注入/学习闭环。

主链路不装配任何 LLM 组件：生成全部透传宿主模型。v2.6 模型调用层（Pipeline /
ModelAdapter / 质量评判 / 回放门禁）归档为附录 C 可选增强——仅当 enhance.* 对应
开关启用时装配（Spec v3.0 §10）。

配置热加载（P1.14）：Runtime 持有 ConfigWatcher；reload 时以新配置重建
检索/召回/学习组件（缓存了配置值的组件随重建生效），DB 连接与日志服务复用。
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
from .data.migrate import migrate
from .data.sqlite import SQLiteClient
from .data.vec import VectorBackend
from .feedback.implicit_tracker import FeedbackWorker
from .injector.conflict import ConflictDetector
from .injector.rule_injector import RuleInjector
from .knowledge.retriever import KnowledgeRetriever
from .learning.knowledge_extractor import KnowledgeExtractor
from .learning.proposal_engine import ProposalEngine
from .learning.skill_loader import SkillLoader
from .learning.snapshot_store import SnapshotStore
from .learning.static_gate import StaticGate
from .project import (
    WORKSPACE_PROJECT_ID,
    load_or_create_identity,
    project_db_path,
    resolve_project_root,
    rsi_home,
)
from .services.decision_queue import DecisionQueue
from .services.knowledge_service import KnowledgeService
from .services.log_service import LogService
from .services.profile_service import ProfileService
from .services.recall_service import RecallService
from .strategy.recall import RecallArmSelector

logger = logging.getLogger(__name__)

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
        db: SQLiteClient,
        vec: Optional[VectorBackend] = None,
        project_root: Optional[Path] = None,
        project_id: Optional[str] = None,
        global_db: Optional[SQLiteClient] = None,
    ):
        self.watcher = watcher
        self.db = db
        self.global_db = global_db or db
        self.project_id = project_id
        self.logs = LogService(db)
        self.profiles = ProfileService(db, global_db=self.global_db)
        # §4.2 异步反馈队列，serve 时 start；profiles 用于 copied/referenced 兴趣加权
        self.feedback_worker = FeedbackWorker(db, profiles=self.profiles)
        # Genome 快照仅依赖 DB 与 ~/.rsi，热加载不重建
        self.snapshots = SnapshotStore(db, rsi_home())
        self._project_root = project_root
        # 向量后端仅增强层使用（enhance.embedding）；启动时创建一次，热加载不重建
        self.vec = vec
        # 抉择队列进程内单例：必须在 _apply_config 之前创建，热加载不得重建 sticky
        self.decisions = DecisionQueue()
        self._apply_config(watcher.config)
        watcher.subscribe(self._apply_config)

    @property
    def config(self) -> dict[str, Any]:
        return self.watcher.config

    def _apply_config(self, config: dict[str, Any]) -> None:
        """配置生效点：脱敏规则 + 依赖配置的组件全部重建（热加载回调）"""
        masking.configure(config)
        enhance = config.get("enhance", {})
        view = _enhance_view(config)

        # ---- 可选增强层（附录 C）：默认全部不装配 ----
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

        # ---- 主链路：检索 / 召回 / 注入 / 学习闭环（零 LLM） ----
        self.retriever = KnowledgeRetriever(
            self.db, config, embedding=embedding, vec=self.vec if embedding else None,
        )
        self.injector = RuleInjector(self.db, project_root=self._project_root)
        self.knowledge = KnowledgeService(
            self.db, self.retriever, embedding=embedding, vec=self.vec if embedding else None,
            bound_project_id=self.project_id,
        )
        self.knowledge.on_change = self.injector.rewrite  # 记忆变更 → 规则文件重写（§4.2）
        self.recall_arms = RecallArmSelector(self.db)
        self.recall = RecallService(
            self.db, self.retriever, self.recall_arms, self.feedback_secret, profiles=self.profiles,
            bound_project_id=self.project_id,
            decisions=self.decisions,
            project_root=self._project_root,
        )
        self.extractor = KnowledgeExtractor(self.db, embedding=embedding, adapter=adapter, config=config)
        gate: Any = StaticGate()
        if enhance.get("gate_replay"):
            if adapter is not None and embedding is not None:
                from .learning.regression_gate import RegressionGate

                gate = RegressionGate(self.db, adapter, embedding, view)
            else:
                logger.warning("enhance.gate_replay 已开启但增强层模型/embedding 未配 Key，回落静态门禁")
        self.proposal_engine = ProposalEngine(
            self.db, adapter, config, gate, self.snapshots, rsi_home(),
            on_change=self.injector.rewrite,
        )
        self.conflict_detector = ConflictDetector(
            self.db, self._project_root, on_change=self.injector.rewrite,
        )
        # 技能槽（§3.9）：包内内置 → ~/.rsi/skills → 项目 .rsi/skills，后者覆盖同名
        skill_dirs = [Path(str(resources.files("rsi_boot") / "config" / "skills")), rsi_home() / "skills"]
        if self._project_root:
            skill_dirs.append(Path(self._project_root) / ".rsi" / "skills")
        self.skill_loader = SkillLoader(skill_dirs)
        # worker 跨热加载存活（保队列），仅替换其质量评估器引用
        self.feedback_worker._quality = quality

    @property
    def feedback_secret(self) -> str:
        return self.config.get("feedback", {}).get("secret_key") or "local-dev-secret"

    async def close(self) -> None:
        try:
            await self.recall.drain()  # 先收敛画像后台任务，再关库
        except Exception:
            pass
        await self.feedback_worker.stop()
        await self.db.close()
        if self.global_db is not self.db:
            await self.global_db.close()


def default_db_path(project_root: Optional[Path] = None) -> Path:
    """当前项目库路径。无参时按 cwd 解析项目根（不再回落 ~/.rsi/rsi.db）。"""
    root = resolve_project_root(explicit=project_root)
    return project_db_path(root)


async def build_runtime(db_path: Optional[Path] = None, project_root: Optional[Path] = None) -> Runtime:
    root = Path(project_root).resolve() if project_root is not None else None
    identity = load_or_create_identity(root) if root is not None else None
    project_id = identity.project_id if identity is not None else None
    resolved_db = Path(db_path) if db_path is not None else (
        project_db_path(root) if root is not None else default_db_path()
    )
    watcher = ConfigWatcher(project_root=root)
    db = SQLiteClient(resolved_db)
    await migrate(db)
    if root is not None and project_id is not None:
        from .services.legacy_migrate import maybe_auto_migrate, remap_to_workspace_id

        await remap_to_workspace_id(db, WORKSPACE_PROJECT_ID)
        await maybe_auto_migrate(root, project_id, db)
    vec = None
    if watcher.config.get("enhance", {}).get("embedding"):
        from .data.vec import create_vector_backend

        vec = await create_vector_backend(db, watcher.config)
    return Runtime(
        watcher, db, vec=vec, project_root=root, project_id=project_id, global_db=db,
    )


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
