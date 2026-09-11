"""标题 → 文件名 slug：保留中英文与数字，其余折叠为连字符。"""

from __future__ import annotations

import re

_NON_WORD = re.compile(r"[^0-9A-Za-z一-鿿]+")
_MAX_LEN = 48


def slugify(title: str) -> str:
    """生成稳定文件名 slug；空结果回退 'item'（同题冲突由调用方加 id 后缀区分）"""
    slug = _NON_WORD.sub("-", title.strip().lower()).strip("-")[:_MAX_LEN].strip("-")
    return slug or "item"
