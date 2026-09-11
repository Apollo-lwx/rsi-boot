"""配置加载（§2.5）：四层配置链 默认 → ~/.rsi/config.yaml → 项目 rsi-boot.yaml → 请求参数。

M1 范围：启动时全量加载合并；热加载（watchfiles）在 M2（P1.14）接入。
"""

from __future__ import annotations

import logging
import os
import re
from importlib import resources
from pathlib import Path
from typing import Any, Optional

import yaml

from ..core.config_merger import merge_configs, validate_config
from ..core.exceptions import ConfigError

logger = logging.getLogger(__name__)

PROJECT_CONFIG_NAME = "rsi-boot.yaml"
ENV_PREFIX = "RSI_"


def _rsi_home() -> Path:
    # 与 bootstrap.rsi_home 一致；本地复制避免循环导入
    return Path(os.environ.get("RSI_HOME", str(Path.home() / ".rsi")))

# §8.2：项目 rsi-boot.yaml 可能入库，禁止出现明文 Key 形态；发现即拒绝启动
_PLAINTEXT_KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
]
_ENV_REF = re.compile(r"^(?:env:|\$\{)([A-Za-z_][A-Za-z0-9_]*)\}?$")


def _contains_plaintext_key(value: Any) -> bool:
    if isinstance(value, str):
        return any(p.search(value) for p in _PLAINTEXT_KEY_PATTERNS)
    if isinstance(value, dict):
        return any(_contains_plaintext_key(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_plaintext_key(v) for v in value)
    return False


def _resolve_env_refs(config: dict[str, Any], source: Path) -> dict[str, Any]:
    """api_key 支持引用环境变量名：`env:MY_KEY` 或 `${MY_KEY}`（§8.2）。
    v3.0：Key 仅在增强层使用，路径为 enhance.model.api_key"""
    enhance = config.get("enhance")
    model_cfg = enhance.get("model") if isinstance(enhance, dict) else None
    if not isinstance(model_cfg, dict):
        return config
    api_key = model_cfg.get("api_key")
    if isinstance(api_key, str):
        match = _ENV_REF.match(api_key.strip())
        if match:
            var_name = match.group(1)
            resolved = os.environ.get(var_name)
            if not resolved:
                raise ConfigError(f"{source} 引用的环境变量 {var_name} 未设置")
            model_cfg["api_key"] = resolved
    return config


#: v3.0 移入 enhance.* 或移除的 v2.6 顶层配置键（检出即提示迁移，主链路忽略）
_LEGACY_KEYS = ("model", "intent", "quality", "gate", "budget", "response_cache", "knowledge")


def _warn_legacy_keys(layer: dict[str, Any], source: Path) -> None:
    hits = [k for k in _LEGACY_KEYS if k in layer]
    if hits:
        logger.warning(
            "%s 含 v2.6 配置命名空间 %s——v3.0 主链路零 Key，LLM 相关配置已迁移至 "
            "enhance.*（见 docs/specs v3.0 附录 C）；旧键在主链路不生效",
            source, ", ".join(hits),
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件解析失败 {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是 mapping: {path}")
    return data


def load_package_default() -> dict[str, Any]:
    text = (resources.files("rsi_boot") / "config" / "default.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def load_model_prices() -> dict[str, dict[str, float]]:
    text = (resources.files("rsi_boot") / "config" / "models.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def load_intent_rules() -> dict[str, Any]:
    text = (resources.files("rsi_boot") / "config" / "intent_rules.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def load_config(project_root: Optional[Path] = None) -> dict[str, Any]:
    """加载并合并文件层配置（请求参数层在 Pipeline 内按请求合并）"""
    layers: list[dict[str, Any]] = [load_package_default()]

    user_cfg = _rsi_home() / "config.yaml"
    user_layer = _resolve_env_refs(_load_yaml(user_cfg), user_cfg)
    _warn_legacy_keys(user_layer, user_cfg)
    layers.append(user_layer)

    if project_root:
        project_cfg_path = Path(project_root) / PROJECT_CONFIG_NAME
        project_cfg = _load_yaml(project_cfg_path)
        _warn_legacy_keys(project_cfg, project_cfg_path)
        # §8.2：项目配置可能入库，禁止明文 Key；发现即拒绝启动
        if _contains_plaintext_key(project_cfg):
            raise ConfigError(
                f"项目配置 {project_cfg_path} 含明文密钥形态（sk-.../AKIA... 等），拒绝启动。"
                f"请将 Key 配置在 ~/.rsi/config.yaml 或使用 env:VAR_NAME 引用环境变量"
            )
        layers.append(project_cfg)

    merged = merge_configs(layers)

    # 环境变量覆盖（仅白名单键，防止意外覆盖关键路径）；v3.0：写入增强层命名空间
    api_key = os.environ.get(f"{ENV_PREFIX}OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key:
        merged.setdefault("enhance", {}).setdefault("model", {})["api_key"] = api_key
    base_url = os.environ.get(f"{ENV_PREFIX}OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if base_url:
        merged.setdefault("enhance", {}).setdefault("model", {})["base_url"] = base_url

    validate_config(merged)
    return merged


class ConfigWatcher:
    """配置热加载（§2.5，P1.14）：watchfiles 监听用户级与项目级配置文件。

    运行时重载策略：last-good-wins——新配置校验失败时保留旧配置并记录错误，
    进程不因坏配置崩溃；修复文件后下一轮变更自动生效。
    """

    def __init__(self, project_root: Optional[Path] = None):
        self._project_root = project_root
        self._config = load_config(project_root=project_root)
        self._listeners: list[Any] = []

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    def subscribe(self, listener: Any) -> None:
        """listener: 同步回调 fn(new_config)，在重载成功后调用"""
        self._listeners.append(listener)

    def reload(self) -> bool:
        """手动重载；校验失败保留旧配置，返回是否生效"""
        try:
            new_config = load_config(project_root=self._project_root)
        except ConfigError as exc:
            logger.error("配置热加载校验失败，保留旧配置: %s", exc)
            return False
        self._config = new_config
        for listener in self._listeners:
            try:
                listener(new_config)
            except Exception:
                logger.exception("配置重载监听器执行失败")
        logger.info("配置已热加载")
        return True

    _WATCHED_NAMES = frozenset({"config.yaml", PROJECT_CONFIG_NAME})

    def _watched_dirs(self) -> list[Path]:
        # 监听目录而非文件：配置文件可能尚不存在（首次创建也要触发热加载）；
        # 目录本身不存在则跳过（rsi init 后 ~/.rsi 必存在）
        dirs = [_rsi_home()]
        if self._project_root:
            dirs.append(Path(self._project_root))
        return [d for d in dirs if d.is_dir()]

    async def watch_loop(self, stop_event: Any = None) -> None:
        """监听配置文件变更并自动重载（serve 模式下作为后台任务运行）"""
        from watchfiles import awatch  # 延迟导入，CLI 一次性命令不要求监听

        dirs = self._watched_dirs()
        if not dirs:
            logger.warning("无可监听的配置目录，热加载未启动")
            return
        logger.info("配置热加载监听中: %s", ", ".join(str(d) for d in dirs))
        async for changes in awatch(*dirs, stop_event=stop_event):
            relevant = [c for c in changes if Path(c[1]).name in self._WATCHED_NAMES]
            if relevant:
                logger.info("检测到配置变更（%d 项），执行热加载", len(relevant))
                self.reload()
