"""Temporal signals for retrieval (T1.1): zero-LLM date extraction and query-time resolution.

写入侧：`extract_iso_dates` 从记忆正文/标题抽出 ISO 日期（上限 per doc），
`build_index` 汇总 `doc_dates` 与语料参考日期 `ref_date`（全库最大日期，无日期时为 None）。

查询侧：`parse_query_temporal` 识别时间意图与显式/相对时间表达，
相对表达按参考日期（语料最大日期；真实使用时为当天）解析成具体窗口。
打分：`date_match_score` 输出 [0,1] 的匹配度，search 里以 (1 + w * m) 乘性提升。

全部确定性实现：正则 + 固定词表，不引入 LLM、不引入第三方日期库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

# ---------------------------------------------------------------- 日期抽取

_ISO_RE = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_ZH_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAMES = sorted(_MONTHS, key=len, reverse=True)
_MONTH_ALT = "|".join(_MONTH_NAMES)
_FULL_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)
_EN_MDY_RE = re.compile(
    rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
    re.IGNORECASE,
)
_EN_DMY_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_ALT})\.?,?\s+(\d{{4}})\b",
    re.IGNORECASE,
)
# 无年份的月日（按参考日期所在年归位，查询扩展用）
_EN_MD_RE = re.compile(
    rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*,?\s*\d{{4}})",
    re.IGNORECASE,
)
# 月份级粒度：in May 2023 / May of 2023 / 2023年5月
_EN_MY_RE = re.compile(
    rf"\b(?:in\s+)?({_MONTH_ALT})\.?\s+(?:of\s+)?(\d{{4}})\b", re.IGNORECASE,
)
_ZH_YM_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月(?!\s*\d)")

MAX_DATES_PER_DOC = 8


def _safe_date(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def extract_iso_dates(text: str, *, cap: int = MAX_DATES_PER_DOC) -> list[str]:
    """从文本抽取 ISO 日期（去重、保序、封顶 cap 个）。"""
    found: list[str] = []
    seen: set[str] = set()

    def _push(iso: str | None) -> None:
        if iso and iso not in seen and len(found) < cap:
            seen.add(iso)
            found.append(iso)

    for match in _ISO_RE.finditer(text or ""):
        _push(_safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
    for match in _ZH_RE.finditer(text or ""):
        _push(_safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
    for match in _EN_MDY_RE.finditer(text or ""):
        _push(_safe_date(
            int(match.group(3)), _MONTHS[match.group(1).lower()], int(match.group(2)),
        ))
    for match in _EN_DMY_RE.finditer(text or ""):
        _push(_safe_date(
            int(match.group(3)), _MONTHS[match.group(2).lower()], int(match.group(1)),
        ))
    return found


def extract_month_grains(text: str) -> list[tuple[int, int]]:
    """月份级时间粒度：May 2023 / 2023年5月。用于 ±月 窗口匹配。"""
    grains: list[tuple[int, int]] = []
    for match in _EN_MY_RE.finditer(text or ""):
        key = (int(match.group(2)), _MONTHS[match.group(1).lower()])
        if key not in grains:
            grains.append(key)
    for match in _ZH_YM_RE.finditer(text or ""):
        key = (int(match.group(1)), int(match.group(2)))
        if 1 <= key[1] <= 12 and key not in grains:
            grains.append(key)
    return grains


# ---------------------------------------------------------------- 查询时间信号

_TEMPORAL_CUES = re.compile(
    r"\b(when|what\s+(?:date|time|year|month|day)|how\s+long\s+ago|"
    r"before|after|since|until|ago|earlier|later|recent(?:ly)?|latest|"
    r"first\s+time|last\s+time)\b"
    r"|什么时候|何时|哪天|哪年|几月|多久以前|多久前|之前|以后|之后|以来|最近|上次|最近一次",
    re.IGNORECASE,
)

# 相对时间表达 → (下界天数偏移, 上界天数偏移)，相对参考日（含端点）
_RELATIVE_PHRASES: list[tuple[re.Pattern[str], tuple[int, int]]] = [
    (re.compile(r"\btoday\b|今天", re.IGNORECASE), (0, 0)),
    (re.compile(r"\byesterday\b|昨天", re.IGNORECASE), (-1, -1)),
    (re.compile(r"\bthe day before yesterday\b|前天", re.IGNORECASE), (-2, -2)),
    (re.compile(r"\blast\s+week\b|上周|上礼拜", re.IGNORECASE), (-7, -1)),
    (re.compile(r"\bthis\s+week\b|本周|这周", re.IGNORECASE), (-7, 0)),
    (re.compile(r"\blast\s+month\b|上个月|上月", re.IGNORECASE), (-31, -1)),
    (re.compile(r"\blast\s+year\b|去年", re.IGNORECASE), (-366, -1)),
    (re.compile(r"\brecent(?:ly)?\b|\blately\b|最近|近期", re.IGNORECASE), (-14, 0)),
    (re.compile(r"\ba\s+(?:few|couple(?:\s+of)?)\s+days\s+ago\b|前几天|几天前", re.IGNORECASE), (-7, -2)),
]
_N_AGO_RE = re.compile(
    r"(\d+)\s+(day|week|month|year)s?\s+ago|(\d+)\s*个?(天|日|周|星期|月|年)前",
    re.IGNORECASE,
)
_N_AGO_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365,
               "日": 1, "天": 1, "周": 7, "星期": 7, "月": 30, "年": 365}


@dataclass(frozen=True)
class TemporalSignal:
    """一次查询的时间信号。windows 为 (start_iso, end_iso) 闭区间。"""
    is_temporal: bool = False
    exact_dates: tuple[str, ...] = ()
    windows: tuple[tuple[str, str], ...] = ()
    month_grains: tuple[tuple[int, int], ...] = ()
    expansions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def bare(self) -> bool:
        """有时间意图但无可解析目标（"when did X happen?"）——改用日期密度提权。"""
        return self.is_temporal and not (
            self.exact_dates or self.windows or self.month_grains
        )


def _month_window(y: int, m: int) -> tuple[str, str]:
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def parse_query_temporal(query: str, reference: date | None) -> TemporalSignal:
    """识别时间意图；显式日期直接收，相对表达按 reference 解析成窗口。"""
    text = query or ""
    is_temporal = bool(_TEMPORAL_CUES.search(text))
    exact = extract_iso_dates(text)
    grains = extract_month_grains(text)
    windows: list[tuple[str, str]] = [_month_window(y, m) for (y, m) in grains]
    expansions: list[str] = []

    ref = reference
    if ref is not None:
        for pattern, (lo, hi) in _RELATIVE_PHRASES:
            if lo == 0 and hi == 0 and not is_temporal:
                continue  # 裸 "today" 不算时间题，避免误提权
            if pattern.search(text):
                windows.append(((ref + timedelta(days=lo)).isoformat(),
                                (ref + timedelta(days=hi)).isoformat()))
        for match in _N_AGO_RE.finditer(text):
            if match.group(1):
                n, unit = int(match.group(1)), match.group(2).lower()
            else:
                n, unit = int(match.group(3)), match.group(4)
            days = n * _N_AGO_DAYS.get(unit, 0)
            if days <= 0:
                continue
            # 点估计 ± 半单位宽
            half = max(_N_AGO_DAYS.get(unit, 1) // 2, 1)
            windows.append(((ref - timedelta(days=days + half)).isoformat(),
                            (ref - timedelta(days=max(days - half, 0))).isoformat()))
        # 无年份月日按参考年归位，补一条 ISO 供扩展
        for match in _EN_MD_RE.finditer(text):
            iso = _safe_date(ref.year, _MONTHS[match.group(1).lower()], int(match.group(2)))
            if iso and iso not in exact:
                expansions.append(iso)

    for iso in exact:
        y, m, d = (int(part) for part in iso.split("-"))
        expansions.append(f"{y}-{m:02d}-{d:02d}")
        month_name = _FULL_MONTH_NAMES[m - 1]
        expansions.append(f"{month_name} {d}")
        expansions.append(f"{d} {month_name}")

    if exact or windows:
        is_temporal = True
    return TemporalSignal(
        is_temporal=is_temporal,
        exact_dates=tuple(exact),
        windows=tuple(windows),
        month_grains=tuple(grains),
        expansions=tuple(dict.fromkeys(expansions)),
    )


# ---------------------------------------------------------------- 匹配打分

_LEADING_DATE_PREFIX = re.compile(r"^\s*\[[^\]\n]*\d{4}[^\]\n]*\]\s*")


def inline_date_count(content: str, *, cap: int = 4) -> int:
    """正文内嵌日期数（去掉行首 [date] 前缀——那只是落库时间锚点，不含信息量）。
    回答"什么时候"的证据句往往自带第二个时间表达，密度是区分信号。"""
    body = _LEADING_DATE_PREFIX_STRIP(content or "")
    return len(extract_iso_dates(body, cap=cap))


def _LEADING_DATE_PREFIX_STRIP(text: str) -> str:
    return _LEADING_DATE_PREFIX.sub("", text, count=1)


def date_match_score(doc_dates: list[str], signal: TemporalSignal) -> float:
    """文档日期与查询信号的匹配度 [0,1]。无信号返回 0。"""
    if not doc_dates or not signal.is_temporal:
        return 0.0
    best = 0.0
    exact = set(signal.exact_dates)
    for iso in doc_dates:
        if iso in exact:
            return 1.0
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            continue
        for start, end in signal.windows:
            if start <= iso <= end:
                best = max(best, 0.8)
                continue
            try:
                lo = date.fromisoformat(start)
                hi = date.fromisoformat(end)
            except ValueError:
                continue
            span = max((hi - lo).days, 1)
            # 窗口边缘外 ±span/2 内给部分分
            if lo - timedelta(days=span // 2) <= d <= hi + timedelta(days=span // 2):
                best = max(best, 0.4)
        for y, m in signal.month_grains:
            if (d.year, d.month) == (y, m):
                best = max(best, 0.6)
    return best
