"""LLM 查询分解（实验特性，RSI_ENABLE=decompose）：把多跳/开放域问题拆成子查询。

设计纪律（与 vector 通道一致）：
- **默认关**：仅在 rsi-boot.yaml `enable: [decompose]` 或 RSI_ENABLE 打开时启用
- **端点**：OpenAI 兼容 chat completions（中转站/官方均可）：
  RSI_DECOMPOSE_BASE_URL / RSI_DECOMPOSE_API_KEY / RSI_DECOMPOSE_MODEL，
  缺省回落 OPENAI_BASE_URL / OPENAI_API_KEY，模型缺省 gpt-4o-mini
- **fail-open**：未配置端点 / 网络异常 / 解析失败 → 返回空列表，
  召回链路按原问题执行，绝不阻塞
- **磁盘缓存**：分解结果按 sha256(model + question) 存
  <rsi_dir>/.cache/decompose.json（RSI_DECOMPOSE_CACHE 可改路径），
  基准复跑零 token、逐位一致
- 子查询上限 3；与原问题重复的子查询丢弃
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

ENV_BASE = "RSI_DECOMPOSE_BASE_URL"
ENV_KEY = "RSI_DECOMPOSE_API_KEY"
ENV_MODEL = "RSI_DECOMPOSE_MODEL"
ENV_CACHE = "RSI_DECOMPOSE_CACHE"
DEFAULT_MODEL = "gpt-4o-mini"
MAX_SUBQUERIES = 3
_TIMEOUT_S = 30

_PROMPT_V = "v3"  # 提示词版本：进缓存键，改提示词自动失效旧缓存
_SYSTEM = (
    "You are a query expansion engine for a memory retrieval system over "
    "long conversations. Rewrite the user question into exactly 2 or 3 "
    "distinct sub-questions that together maximize the chance of retrieving "
    "the relevant conversation turns:\n"
    "- For multi-hop or comparison questions: decompose into the individual "
    "facts that must be found (one sub-question per fact).\n"
    "- For simple questions: produce alternative phrasings that surface "
    "different aspects — the people, dates, places, actions involved.\n"
    "Rules:\n"
    "- Name people, entities, places and dates explicitly; never use "
    "pronouns (he/she/it/they) or vague references (there, that day).\n"
    "- Each sub-question must stand alone and be answerable from a single "
    "conversation turn.\n"
    "- Output ONLY a JSON array of 2-3 strings. Never return the original "
    "question unchanged."
)

_memo: Dict[str, List[str]] = {}
_memo_path: Optional[Path] = None
_lock = threading.Lock()  # 并发预热时 memo 更新与落盘互斥（Windows 对 replace 敏感）


def _cache_path(rsi_dir: Path) -> Path:
    raw = os.environ.get(ENV_CACHE)
    if raw:
        return Path(raw)
    return Path(rsi_dir) / ".cache" / "decompose.json"


def _load_cache(path: Path) -> None:
    global _memo, _memo_path
    if _memo_path == path:
        return
    _memo_path = path
    _memo = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _memo = {str(k): [str(x) for x in v] for k, v in data.items()
                     if isinstance(v, list)}
    except (OSError, json.JSONDecodeError):
        # 损坏文件备份保留（追责/恢复），从空缓存重来
        try:
            if path.exists():
                backup = path.with_suffix(".corrupt.json")
                path.replace(backup)
                logger.warning("decompose 缓存损坏，已备份到 %s", backup)
        except OSError:
            pass


def _save_cache(path: Path) -> None:
    """原子落盘：tmp 名按进程+线程唯一，避免并发预热时互相截断。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(
            f"{path.stem}.{os.getpid()}-{threading.get_ident()}.tmp"
        )
        tmp.write_text(
            json.dumps(_memo, ensure_ascii=False, indent=0), encoding="utf-8"
        )
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("decompose 缓存写入失败: %s", exc)


def _endpoint() -> Optional[tuple[str, str, str]]:
    base = os.environ.get(ENV_BASE) or os.environ.get("OPENAI_BASE_URL") or ""
    key = os.environ.get(ENV_KEY) or os.environ.get("OPENAI_API_KEY") or ""
    model = os.environ.get(ENV_MODEL) or DEFAULT_MODEL
    if not base or not key:
        return None
    return base, key, model


def _parse_subqueries(text: str, question: str) -> List[str]:
    """从模型输出抠 JSON 数组；容错到逐行拆分。与原问题重复的丢弃。"""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    candidates: List[str] = []
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                candidates = [str(x) for x in data if isinstance(x, (str, int, float))]
        except json.JSONDecodeError:
            candidates = []
    if not candidates:
        candidates = [
            ln.strip().lstrip("0123456789.-) ").strip()
            for ln in text.splitlines()
            if ln.strip()
        ]
    norm = question.strip().rstrip("?").lower()
    out: List[str] = []
    for cand in candidates:
        cand = cand.strip()
        if cand and cand.rstrip("?").lower() != norm:
            out.append(cand)
    return out[:MAX_SUBQUERIES]


def _call_llm(question: str, base: str, key: str, model: str) -> Optional[str]:
    from openai import OpenAI  # 延迟导入：openai 是硬依赖，但只在启用时才用

    client = OpenAI(base_url=base, api_key=key, timeout=_TIMEOUT_S, max_retries=1)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": question},
        ],
        # 不传 temperature：kimi-k3 等端点只接受默认值 1；确定性靠磁盘缓存保证
        max_tokens=300,
    )
    choice = resp.choices[0] if resp.choices else None
    content = getattr(getattr(choice, "message", None), "content", None)
    return str(content) if content else None


def decompose(question: str, rsi_dir: Path) -> List[str]:
    """同步分解：命中缓存零网络；任何失败返回 []（调用方按原问题召回）。"""
    question = (question or "").strip()
    if not question:
        return []
    ep = _endpoint()
    cache = _cache_path(rsi_dir)
    model = ep[2] if ep else DEFAULT_MODEL
    sha = hashlib.sha256(f"{_PROMPT_V}\0{model}\0{question}".encode("utf-8")).hexdigest()
    with _lock:
        _load_cache(cache)
        if sha in _memo:
            return list(_memo[sha])
    if ep is None:
        logger.warning("decompose 已启用但未配置端点（%s/%s 或 OPENAI_*），跳过",
                       ENV_BASE, ENV_KEY)
        return []
    base, key, _ = ep
    try:
        raw = _call_llm(question, base, key, model)
        subs = _parse_subqueries(raw, question) if raw else []
    except Exception as exc:
        # 瞬态故障不缓存：下次重试；只有模型真实应答（含空分解）才落缓存
        logger.warning("查询分解失败（按原问题召回）: %s", exc)
        return []
    with _lock:
        _memo[sha] = subs
        _save_cache(cache)
    return subs


async def decompose_async(question: str, rsi_dir: Path) -> List[str]:
    """异步包装：网络与缓存 IO 移出事件循环。"""
    import asyncio

    return await asyncio.to_thread(decompose, question, rsi_dir)
