"""意图识别（§3.1 + §3.6 插件化角色意图注册）。

规则版匹配顺序（§3.6 运行时行为）：role 专属意图 → 通用意图 → fallback。
role=None 时仅用通用意图（开发者兼容模式）。
规则来源：包内 config/intent_rules.yaml（通用）+ config/roles/<role>.yaml（角色）
+ ~/.rsi/intent_rules.yaml（Harness 提案写入的用户覆写层，§3.9 intent_rule 槽；
优先级高于包内通用规则，mtime 变化即重载）。

P2.2 LLM few-shot 升级（§3.1 Phase 2）：detect_intent_llm 用 gpt-4o-mini
（temperature=0，max_tokens=50，超时 3s）分类，强制 JSON 输出契约；
降级链：LLM 超时/失败/JSON 解析失败 → 规则版 detect_intent（永远可用）；
LLM confidence < 0.6 → 丢弃结果回退 fallback（0.5）。结果进程内缓存 TTL 300s。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from cachetools import TTLCache

from ..config.loader import load_intent_rules

logger = logging.getLogger(__name__)

_CompiledRule = Tuple[re.Pattern[str], str, float]

_GENERAL_RULES: Optional[List[_CompiledRule]] = None
_ROLE_RULES: Optional[Dict[str, List[_CompiledRule]]] = None
_FALLBACK: Tuple[str, float] = ("general_assist", 0.5)
# 用户覆写层（§3.9 intent_rule 槽提案落点）：路径 + mtime 缓存
_OVERLAY_PATH: Optional[Path] = None
_OVERLAY_RULES: List[_CompiledRule] = []
_OVERLAY_MTIME: Optional[int] = None


def _overlay_path() -> Path:
    global _OVERLAY_PATH
    if _OVERLAY_PATH is None:
        _OVERLAY_PATH = Path(os.environ.get("RSI_HOME", str(Path.home() / ".rsi"))) / "intent_rules.yaml"
    return _OVERLAY_PATH


def _load_overlay() -> List[_CompiledRule]:
    """用户覆写规则（mtime 变化即重载；提案应用后下一轮请求生效）"""
    global _OVERLAY_RULES, _OVERLAY_MTIME
    path = _overlay_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        _OVERLAY_RULES, _OVERLAY_MTIME = [], None
        return _OVERLAY_RULES
    if mtime == _OVERLAY_MTIME:
        return _OVERLAY_RULES
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rules = [
            (re.compile(r["pattern"], re.IGNORECASE), r["intent"], float(r.get("confidence", 0.85)))
            for r in data.get("rules", [])
            if isinstance(r, dict) and r.get("pattern") and r.get("intent")
        ]
    except Exception as exc:
        logger.warning("意图覆写规则加载失败（忽略覆写层）: %s", exc)
        rules = []
    _OVERLAY_RULES, _OVERLAY_MTIME = rules, mtime
    return _OVERLAY_RULES


def _compile_rules(rules: list[dict[str, Any]], default_confidence: float) -> List[_CompiledRule]:
    return [
        (re.compile(rule["pattern"], re.IGNORECASE), rule["intent"], float(rule.get("confidence", default_confidence)))
        for rule in rules
    ]


def _load_role_rules() -> Dict[str, List[_CompiledRule]]:
    """加载 config/roles/*.yaml 的角色意图（§3.6 注册方式：patterns 列表 + fallback）"""
    role_rules: Dict[str, List[_CompiledRule]] = {}
    roles_dir = resources.files("rsi_boot") / "config" / "roles"
    if not roles_dir.is_dir():
        return role_rules
    for entry in roles_dir.iterdir():
        if not entry.name.endswith(".yaml"):
            continue
        data = yaml.safe_load(entry.read_text(encoding="utf-8")) or {}
        compiled: List[_CompiledRule] = []
        for intent_def in data.get("intents", []):
            for pattern in intent_def.get("patterns", []):
                compiled.append((re.compile(pattern, re.IGNORECASE), intent_def["name"], 0.85))
        if data.get("role"):
            role_rules[data["role"]] = compiled
    return role_rules


def _ensure_loaded() -> None:
    global _GENERAL_RULES, _ROLE_RULES, _FALLBACK
    if _GENERAL_RULES is not None:
        return
    data: dict[str, Any] = load_intent_rules()
    _GENERAL_RULES = _compile_rules(data.get("rules", []), default_confidence=0.8)
    fb = data.get("fallback") or {}
    _FALLBACK = (fb.get("intent", "general_assist"), float(fb.get("confidence", 0.5)))
    _ROLE_RULES = _load_role_rules()


def detect_intent(raw_input: str, role: Optional[str] = None) -> Tuple[str, float]:
    """先匹配 role 专属意图，再匹配用户覆写层，再匹配通用意图，最后兜底"""
    _ensure_loaded()
    assert _GENERAL_RULES is not None and _ROLE_RULES is not None
    if role:
        for pattern, intent, confidence in _ROLE_RULES.get(role, []):
            if pattern.search(raw_input):
                return intent, confidence
    for pattern, intent, confidence in _load_overlay():
        if pattern.search(raw_input):
            return intent, confidence
    for pattern, intent, confidence in _GENERAL_RULES:
        if pattern.search(raw_input):
            return intent, confidence
    return _FALLBACK


def registered_roles() -> List[str]:
    _ensure_loaded()
    assert _ROLE_RULES is not None
    return sorted(_ROLE_RULES)


# ---------- P2.2：LLM few-shot 分类（§3.1 Phase 2） ----------

_EXAMPLES: Optional[Dict[str, Any]] = None
# §2.4 进程内缓存（原 Redis key rsi:cache:intent:* 的本地化形态）
_INTENT_CACHE: TTLCache = TTLCache(maxsize=500, ttl=300)
_LLM_TIMEOUT_S = 3.0
_LLM_MIN_CONFIDENCE = 0.6
_MAX_INPUT_CHARS = 2000


def _load_examples() -> Dict[str, Any]:
    global _EXAMPLES
    if _EXAMPLES is None:
        path = resources.files("rsi_boot") / "config" / "intent_examples.yaml"
        _EXAMPLES = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _EXAMPLES


def _intent_catalog(role: Optional[str]) -> Dict[str, Any]:
    """角色请求用角色意图集（§3.6），否则用通用意图集"""
    data = _load_examples()
    if role:
        catalog = data.get("roles", {}).get(role)
        if catalog:
            return catalog
    return data.get("general", {})


def _build_llm_messages(raw_input: str, catalog: Dict[str, Any]) -> List[dict[str, str]]:
    lines = ["意图清单与定义："]
    for name, meta in catalog.items():
        lines.append(f"- {name}: {meta.get('definition', '')}")
    lines.append('输出格式（严格 JSON，无其他文本）：{"intent": "<intent>", "confidence": 0.0~1.0}')
    system_prompt = "你是意图分类器。将用户输入分类到以下意图之一。\n" + "\n".join(lines)

    shots: List[str] = []
    for name, meta in catalog.items():
        for example in (meta.get("examples") or [])[:2]:
            shots.append(f"输入：{example}\n输出：{{\"intent\": \"{name}\", \"confidence\": 0.9}}")

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "示例：\n" + "\n\n".join(shots)},
        {"role": "user", "content": f"输入：{raw_input[:_MAX_INPUT_CHARS]}\n输出："},
    ]


def _parse_llm_output(text: str) -> Optional[Tuple[str, float]]:
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        intent = str(data["intent"])
        confidence = float(data["confidence"])
    except (ValueError, KeyError, TypeError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    return intent, confidence


async def detect_intent_llm(
    raw_input: str,
    role: Optional[str] = None,
    adapter: Any = None,
    config: Optional[dict[str, Any]] = None,
) -> Tuple[str, float]:
    """LLM few-shot 分类；任何失败/低置信度均降级（规则版或 fallback），不抛异常"""
    if adapter is None:
        return detect_intent(raw_input, role=role)

    cache_key = f"{role or ''}:{hashlib.md5(raw_input[:512].encode('utf-8')).hexdigest()}"
    cached = _INTENT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    catalog = _intent_catalog(role)
    model = str((config or {}).get("intent", {}).get("model", "gpt-4o-mini"))
    try:
        result = await asyncio.wait_for(
            adapter.complete(model, _build_llm_messages(raw_input, catalog)),
            timeout=_LLM_TIMEOUT_S,
        )
        parsed = _parse_llm_output(result.text or "")
    except Exception as exc:
        logger.info("LLM 意图分类失败（降级规则版）: %s", exc)
        return detect_intent(raw_input, role=role)

    if parsed is None or parsed[0] not in catalog:
        logger.info("LLM 意图输出解析失败/意图非法（降级规则版）")
        return detect_intent(raw_input, role=role)

    intent, confidence = parsed
    # confidence < 0.6 丢弃结果，回退 fallback（0.5）；通用意图是所有角色的兜底（§3.6）
    outcome = (intent, confidence) if confidence >= _LLM_MIN_CONFIDENCE else _FALLBACK
    _INTENT_CACHE[cache_key] = outcome
    return outcome
