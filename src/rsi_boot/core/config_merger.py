"""配置合并（§2.5）：链式继承 + 覆盖。

合并策略：
- 默认合并深度 2 层（浅合并 + 一层嵌套）
- 列表字段替换（非追加）
- 关键路径（model.endpoint / model.api_key 等）不允许请求参数层覆盖
"""

from __future__ import annotations

import copy
from typing import Any, Iterable

from .exceptions import ConfigError

# 请求参数层禁止覆盖的关键路径（仅文件层可配）；v3.0：模型 Key 类路径在增强层命名空间
PROTECTED_PATHS = frozenset({
    "enhance.model.endpoint", "enhance.model.api_key", "enhance.model.base_url", "feedback.secret_key",
})

_MAX_DEPTH = 2


def _merge_two(base: dict[str, Any], override: dict[str, Any], depth: int, path: str, allow_protected: bool) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        full_path = f"{path}.{key}" if path else key
        if not allow_protected and full_path in PROTECTED_PATHS:
            continue
        if depth < _MAX_DEPTH and isinstance(value, dict):
            # 即使 base 中不存在该节点也递归，确保嵌套受保护路径被过滤
            base_node = result.get(key) if isinstance(result.get(key), dict) else {}
            result[key] = _merge_two(base_node, value, depth + 1, full_path, allow_protected)
        else:
            # 列表/标量/超深度嵌套：整体替换
            result[key] = copy.deepcopy(value)
    return result


def merge_configs(layers: Iterable[dict[str, Any]], allow_protected: bool = True) -> dict[str, Any]:
    """按优先级从低到高合并配置层。文件层 allow_protected=True；请求参数层调用时传 False"""
    merged: dict[str, Any] = {}
    for layer in layers:
        if layer:
            merged = _merge_two(merged, layer, depth=0, path="", allow_protected=allow_protected)
    return merged


def merge_request_overrides(file_config: dict[str, Any], request_overrides: dict[str, Any]) -> dict[str, Any]:
    """请求参数层合并：仅当次生效，关键路径受保护"""
    return _merge_two(file_config, request_overrides, depth=0, path="", allow_protected=False)


# 已知配置路径的类型与取值约束（§2.5 类型验证 + 错误报告）；v3.0：LLM 类路径在 enhance.*
_CONFIG_SCHEMA: dict[str, tuple[type, ...]] = {
    "enhance.model.default": (str,),
    "enhance.model.timeout_s": (int, float),
    "enhance.model.max_retries": (int,),
    "enhance.model.api_key": (str,),
    "enhance.model.base_url": (str,),
    "retrieval.top_n": (int,),
    "retrieval.token_budget": (int,),
    "retrieval.bm25_threshold": (int, float),
    "feedback.secret_key": (str,),
    "bootstrap.review_queue_cap": (int,),
}

_RANGES: dict[str, tuple[float, float]] = {
    "enhance.model.timeout_s": (1, 600),
    "enhance.model.max_retries": (0, 10),
    "retrieval.top_n": (1, 20),
    "retrieval.token_budget": (100, 32000),
    "retrieval.bm25_threshold": (0.0, 1.0),
    "bootstrap.review_queue_cap": (1, 10000),
}


def validate_config(config: dict[str, Any]) -> None:
    """校验已知路径的类型与取值范围；聚合全部违规后一次性报出"""
    violations: list[str] = []
    for path, expected in _CONFIG_SCHEMA.items():
        node: Any = config
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if node is None:
            continue
        # bool 是 int 子类，配置语义上不接受
        if isinstance(node, bool) or not isinstance(node, expected):
            names = "/".join(t.__name__ for t in expected)
            violations.append(f"{path}: 期望 {names}，实际 {type(node).__name__}({node!r})")
            continue
        if path in _RANGES and isinstance(node, (int, float)):
            low, high = _RANGES[path]
            if not low <= node <= high:
                violations.append(f"{path}: 取值 {node} 超出范围 [{low}, {high}]")
    if violations:
        raise ConfigError("配置校验失败：\n" + "\n".join(f"  - {v}" for v in violations))
