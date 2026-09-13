"""User-facing copy, language detection, and tool descriptions."""

from rsi_boot.ux.lang import detect_lang, locale_lang, preserve_spans
from rsi_boot.ux.messages import STRINGS, TOOL_DESC, t

__all__ = [
    "STRINGS",
    "TOOL_DESC",
    "detect_lang",
    "locale_lang",
    "preserve_spans",
    "t",
]
