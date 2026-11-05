"""消融开关（T1.3）：让论文的 ablation study 成为配置，不改代码。

开关来源（优先级从高到低）：
1. 环境变量 RSI_ABLATE="temporal,context"（benchmark 用）
2. 项目 rsi-boot.yaml 的 `ablation: [temporal, ...]` 段（随仓库入库，可复现）

已知键：
- expansion   关掉 expand_query 的同义词/SQL 扩展（意图标签保留）
- title       标题字段权重退回 1.0（与正文同权）
- weakfilter  关掉 drop_weak 的强词过滤与相对顶分门槛
- temporal    关掉时间信号提权与日期扩展
- context     关掉基准写入侧的 turn 级上下文增强（benchmark 专用）
- diversity   关掉检索结果的 MMR 多样性去重
- quotediscount 关掉时间题查询期的引用行折权（默认开：temporal 查询把
              context:/引用 等显式拷贝行的词项贡献折到 0.15）

未知名键直接忽略（前向兼容：新版开关在旧代码上不起作用，而不是报错）。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

ENV_KEY = "RSI_ABLATE"
KNOWN_KEYS = frozenset({
    "expansion", "title", "weakfilter", "temporal", "context", "diversity",
    "quotediscount",
})

_cache: dict[tuple[str, float], frozenset[str]] = {}


def _from_env() -> frozenset[str] | None:
    raw = os.environ.get(ENV_KEY)
    if raw is None:
        return None
    return frozenset(
        item.strip() for item in raw.split(",") if item.strip() in KNOWN_KEYS
    )


def _from_project(project_root: Path | None) -> frozenset[str]:
    if project_root is None:
        return frozenset()
    path = Path(project_root) / "rsi-boot.yaml"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return frozenset()
    key = (str(path), mtime)
    if key in _cache:
        return _cache[key]
    keys: frozenset[str] = frozenset()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            raw = data.get("ablation") or []
            if isinstance(raw, list):
                keys = frozenset(str(item) for item in raw if str(item) in KNOWN_KEYS)
    except (yaml.YAMLError, OSError):
        keys = frozenset()
    _cache.clear()  # 配置可能已整体切换，简单起见只留最新一条
    _cache[key] = keys
    return keys


def active_ablations(project_root: Path | None = None) -> frozenset[str]:
    """当前生效的消融键集合。"""
    env = _from_env()
    if env is not None:
        return env
    return _from_project(project_root)


def is_ablated(name: str, project_root: Path | None = None) -> bool:
    return name in active_ablations(project_root)


# 实验特性默认关（如实测为负的 diversity）；通过 RSI_ENABLE 或 yaml `enable:` 打开
# 已知 enable 键：
# - diversity   MMR 多样性去重（实测误伤正当证据，见 benchmark/results/abl-mmr）
# - coverage    查询词项覆盖率重排
# - vector      fastembed/ONNX 本地向量与词面通道融合（rag/vector.py）
# - multihop    首跳桥接词发起第二跳级联召回（多跳题）
ENABLE_ENV = "RSI_ENABLE"


def is_enabled(name: str, project_root: Path | None = None) -> bool:
    raw = os.environ.get(ENABLE_ENV)
    if raw is not None:
        return name in {item.strip() for item in raw.split(",") if item.strip()}
    if project_root is None:
        return False
    path = Path(project_root) / "rsi-boot.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    raw_list = data.get("enable") or []
    if not isinstance(raw_list, list):
        return False
    return name in {str(item) for item in raw_list}
