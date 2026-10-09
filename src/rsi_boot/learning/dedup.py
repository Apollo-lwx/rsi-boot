"""记忆之间的近重复检测（改进点 3：零 Key 语义去重/冲突前置提示）。

背景：既有去重只在标题完全相等时合并（learning/knowledge_extractor.py），
且 conflict.py 只治理「学习记忆 vs 用户手写规则」。这里补上「记忆 vs 记忆」的
近重复发现：写入或晋升前，用新条目自身的标题+正文在同类型正式记忆里找近邻，
命中高重叠即返回候选，交由宿主用 rsi_conflicts / rsi_knowledge_review 裁决，
避免同义记忆越堆越多挤占 rsi_recall 的 TopN 与预算。

零 Key：用 rag.index.tokenize（CJK bigram + ASCII 词）做词袋 Jaccard 重叠，
不依赖 embedding；配 Key 时可另行升级为向量近邻（附录 C 增强，此处不涉及）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..memory.harvest import is_harvest_doc
from ..memory.store import MemoryStore
from ..rag.index import tokenize

#: 判定「疑似近重复」的词袋 Jaccard 重叠下限
DEFAULT_OVERLAP = 0.6
_MAX_RESULTS = 5
#: 超长正文先截断再分词：近重复判定用词袋重叠，无需整篇 tokenize（性能护栏）
_MAX_BAG_CHARS = 4000


def _bag(title: str, content: str) -> set:
    text = f"{title}\n{(content or '')}"[:_MAX_BAG_CHARS]
    return set(tokenize(text.lower()))


def promote_block_config(config: Any) -> tuple[bool, float]:
    """读取晋升近重复治理配置 dedup.block_on_promote / overlap_threshold。
    缺省（无 config / 非映射 / 非法值）返回 (False, DEFAULT_OVERLAP) 且不阻断，
    阈值夹紧到 [0,1]。Tool 与 CLI 共用此单一定义，避免语义漂移。"""
    cfg = config if isinstance(config, dict) else None
    if not cfg:
        return False, DEFAULT_OVERLAP
    dedup = cfg.get("dedup")
    if not isinstance(dedup, dict):
        return False, DEFAULT_OVERLAP
    try:
        threshold = min(1.0, max(0.0, float(dedup.get("overlap_threshold", DEFAULT_OVERLAP))))
    except (TypeError, ValueError):
        threshold = DEFAULT_OVERLAP
    return bool(dedup.get("block_on_promote")), threshold


def overlap(new_bag: set, other_bag: set) -> float:
    """Jaccard 相似度：交集 / 并集；任一为空返回 0。"""
    if not new_bag or not other_bag:
        return 0.0
    inter = len(new_bag & other_bag)
    union = len(new_bag | other_bag)
    return inter / union if union else 0.0


def find_near_duplicates(
    store: MemoryStore,
    *,
    title: str,
    content: str,
    content_type: str,
    exclude_id: Optional[str] = None,
    threshold: float = DEFAULT_OVERLAP,
) -> List[Dict[str, Any]]:
    """在正式（active）同类型记忆中查找与 (title, content) 近重复的条目。

    返回按重叠度降序、至多 5 条：[{id, title, overlap, path}]。exclude_id 用于
    晋升自检时排除自身。低重叠（< threshold）不返回，宁缺毋滥避免误报。
    """
    new_bag = _bag(title, content)
    if not new_bag:
        return []
    hits: List[Dict[str, Any]] = []
    for doc in store.list_official(content_type, skip_harvest=True):
        if doc.status != "active" or doc.id == exclude_id:
            continue
        if is_harvest_doc(doc):
            continue
        score = overlap(new_bag, _bag(doc.title, doc.content))
        if score >= threshold:
            hits.append({
                "id": doc.id,
                "title": doc.title,
                "overlap": round(score, 3),
                "path": doc.path,
            })
    hits.sort(key=lambda row: row["overlap"], reverse=True)
    return hits[:_MAX_RESULTS]


# ---------------------------------------------------------------------------
# 批量晋升池（review I4）：批量审批时若逐条 find_promotion_duplicates，
# 内部会重复 `store.list_official + yaml.safe_load + tokenize` → K×M 全库重解析，
# 且同步 IO 会阻塞 MCP 事件循环。这里给一个"一次预载、内存比对、就地追加"的池：
# - build_promotion_pool：按 MEMORY_TYPES 拉一次 active 正式库，预算每篇词袋
# - pool_check：拿池做近重复，等价 find_near_duplicates，但不重扫盘
# - pool_add：晋升成功后追加，天然覆盖同批互重
# 调用方用 asyncio.to_thread 包一层，把 tokenize 移出事件循环。
# ---------------------------------------------------------------------------


def build_promotion_pool(store: MemoryStore, types: Optional[List[str]] = None) -> Dict[str, List[Dict[str, Any]]]:
    """一次性把所有相关类型的 active 正式记忆读进内存，附预算词袋。

    返回 {content_type: [{id,title,path,bag}, ...]}。skip_harvest 与 harvest 双重
    过滤确保 harvest 条目不入池（对齐 find_near_duplicates 语义）。"""
    if types is None:
        from ..memory.types import MEMORY_TYPES

        types = list(MEMORY_TYPES)
    pool: Dict[str, List[Dict[str, Any]]] = {}
    for typ in types:
        bucket = pool.setdefault(typ, [])
        for doc in store.list_official(typ, skip_harvest=True):
            if doc.status != "active" or is_harvest_doc(doc):
                continue
            bucket.append({
                "id": doc.id,
                "title": doc.title,
                "path": doc.path,
                "bag": _bag(doc.title, doc.content),
            })
    return pool


def pool_check(
    pool: Dict[str, List[Dict[str, Any]]],
    doc: Any,
    *,
    threshold: float = DEFAULT_OVERLAP,
) -> List[Dict[str, Any]]:
    """基于已预载 pool 判断 doc 是否与既有正式项近重复。等价 find_near_duplicates，
    但不做二次磁盘 IO。同 id 自动排除（用于同批刚晋升的复检）。"""
    new_bag = _bag(doc.title, doc.content)
    if not new_bag:
        return []
    hits: List[Dict[str, Any]] = []
    for row in pool.get(getattr(doc, "type", "") or "", []):
        if row["id"] == doc.id:
            continue
        score = overlap(new_bag, row["bag"])
        if score >= threshold:
            hits.append({
                "id": row["id"],
                "title": row["title"],
                "overlap": round(score, 3),
                "path": row["path"],
            })
    hits.sort(key=lambda row: row["overlap"], reverse=True)
    return hits[:_MAX_RESULTS]


def pool_add(pool: Dict[str, List[Dict[str, Any]]], doc: Any) -> None:
    """晋升成功后把 doc 追加进池，后续同批次条目自然能与其比对。"""
    bucket = pool.setdefault(getattr(doc, "type", "") or "", [])
    bucket.append({
        "id": doc.id,
        "title": doc.title,
        "path": doc.path,
        "bag": _bag(doc.title, doc.content),
    })
