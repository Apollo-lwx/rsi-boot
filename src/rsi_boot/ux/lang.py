"""Detect caller language; strip literal spans before counting Han vs letters."""

from __future__ import annotations

import locale
import os
import re
import sys
from typing import Literal

from rsi_boot.ux.terms import collect_terms

Lang = Literal["zh", "en"]

_SQL_KEYWORDS = (
    "SELECT|FROM|WHERE|INSERT|UPDATE|DELETE|JOIN|INTO|VALUES|GROUP|ORDER|"
    "LIMIT|OFFSET|CREATE|TABLE|INDEX|DROP|ALTER|HAVING|UNION|DISTINCT|"
    "INNER|OUTER|LEFT|RIGHT|BETWEEN|EXISTS|LIKE"
)

_FENCE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_RSI_TOOL_RE = re.compile(r"\brsi_[A-Za-z0-9_]+\b")
_WIN_PATH_RE = re.compile(r"(?:[A-Za-z]:)(?:[/\\][\w.\-]+)+")
_POSIX_PATH_RE = re.compile(r"(?:\.{0,2}/)?[\w.\-]+(?:/[\w.\-]+)+")
_ALLCAPS_RE = re.compile(r"\b[A-Z]{2,}[A-Z0-9_]*\b")
_SQL_RE = re.compile(rf"\b(?:{_SQL_KEYWORDS})\b", re.IGNORECASE)

_last_lang: Lang | None = None


def preserve_spans(text: str, extra_terms: list[str] | None = None) -> str:
    """返回去掉原样片段后的文本，供探测。不改调用方的原文。"""
    out = text
    out = _FENCE_RE.sub(" ", out)
    out = _INLINE_CODE_RE.sub(" ", out)
    out = _RSI_TOOL_RE.sub(" ", out)
    out = _WIN_PATH_RE.sub(" ", out)
    out = _POSIX_PATH_RE.sub(" ", out)
    out = _ALLCAPS_RE.sub(" ", out)
    out = _SQL_RE.sub(" ", out)
    for term in sorted(collect_terms(extra_terms), key=len, reverse=True):
        if not term:
            continue
        out = re.sub(re.escape(term), " ", out, flags=re.IGNORECASE)
    return out


def locale_lang() -> Lang:
    env = (os.environ.get("RSI_LANG") or "").strip().lower()
    if env in ("zh", "en"):
        return env  # type: ignore[return-value]
    candidates: list[str] = []
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(key)
        if value:
            candidates.append(value)
    try:
        loc = locale.getlocale()
    except ValueError:
        loc = (None, None)
    if loc and loc[0]:
        candidates.append(loc[0])
    if sys.platform == "win32":
        try:
            import ctypes

            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = langid & 0x3FF
            if primary == 0x04:
                candidates.append("zh")
            elif primary == 0x09:
                candidates.append("en")
        except (AttributeError, OSError):
            pass
    for cand in candidates:
        low = cand.lower()
        if low.startswith("zh") or "chinese" in low or "china" in low:
            return "zh"
        if low.startswith("en"):
            return "en"
    return "en"


def detect_lang(*texts: str, explicit: str | None = None) -> Lang:
    """先 preserve_spans，再：explicit → han>=2 则 zh → 有字母则 en → 进程上次 → OS locale → en。"""
    global _last_lang
    if explicit in ("zh", "en"):
        _last_lang = explicit
        return explicit  # type: ignore[return-value]
    remainder = preserve_spans(" ".join(texts))
    han = sum(1 for ch in remainder if "\u4e00" <= ch <= "\u9fff")
    if han >= 2:
        found: Lang = "zh"
    elif any(ch.isalpha() for ch in remainder):
        found = "en"
    elif _last_lang is not None:
        found = _last_lang
    else:
        found = locale_lang()
    _last_lang = found
    return found
