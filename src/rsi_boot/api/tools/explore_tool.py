"""rsi_explore 工具（对标 Qoder 知识树 explore）：按 domain 板块下钻浏览记忆层级。

被动注入的 rsi-tree 只给顶层板块+计数；宿主据此判断方向后，用本工具逐层下钻拿到
成员条目标题/简述/id，再用 rsi_recall 取正文。树与注入端共用 memory.tree.load_tree，
两端板块结构一致。下钻具体板块时同时并入 memory/graph 的图谱边（related/conflicts）。
"""

from __future__ import annotations

from typing import Any

from ...ux.messages import TOOL_DESC

TOOL_NAME = "rsi_explore"

TOOL_DESCRIPTION = TOOL_DESC["explore"]

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "知识树节点路径；空 = 列顶层板块，如 backend 或 backend/api = 下钻该板块",
        },
        "depth": {
            "type": "integer", "minimum": 1, "maximum": 4,
            "description": "返回的子树层级深度（默认 2）",
        },
        "limit": {
            "type": "integer", "minimum": 1, "maximum": 100,
            "description": "最多列出成员条目数（默认 50）",
        },
    },
    "required": [],
}


def _child_nodes(node: Any, depth: int) -> list[dict[str, Any]]:
    if depth <= 0:
        return []
    return [
        {
            "name": child.name,
            "path": child.path,
            "total": child.total,
            "children": _child_nodes(child, depth - 1),
        }
        for child in node.sorted_children()
    ]


async def handle(runtime: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    store = getattr(runtime, "store", None)
    if store is None:
        return {"status": "error", "message": "缺少记忆库句柄"}
    from ...memory.tree import TREE_TYPES, build_tree, find_node

    path = str(arguments.get("path") or "").strip().strip("/\\")
    path = path.replace("\\", "/").strip("/")
    depth = _as_int(arguments.get("depth"), default=2, lo=1, hi=4)
    limit = _as_int(arguments.get("limit"), default=50, lo=1, hi=100)

    # 一次扫描把 active 正式记忆读进内存，同时喂给 build_tree 与 _related_edges，
    # 避免逐边 store.read 引发重复解析与 _sync_status_to_disk 的写副作用（review S5）。
    active_docs: list[Any] = []
    for typ in TREE_TYPES:
        for doc in store.list_official(typ, skip_harvest=True):
            if getattr(doc, "status", "active") == "active":
                active_docs.append(doc)
    root = build_tree(active_docs)
    node = find_node(root, path)
    if node is None:
        return {
            "status": "success",
            "data": {
                "path": path,
                "exists": False,
                "children": _child_nodes(root, 1),
                "items": [],
                "message": "未知板块；children 给出顶层板块，或用 rsi_recall 直接检索",
            },
        }
    items = node.descendant_items(limit=limit)
    data: dict[str, Any] = {
        "path": path or "/",
        "exists": True,
        "total": node.total,
        "children": _child_nodes(node, depth),
        "items": items,
    }
    # 缺口 #2：下钻具体板块时并入图谱边（顶层概览不扫，避免每板块全库读）
    if path:
        ids = [it.get("id") for it in items if isinstance(it, dict) and it.get("id")]
        doc_index = {d.id: d for d in active_docs}
        related, conflicts, r_trunc, c_trunc = _related_edges(store, ids, doc_index)
        data["related"] = related
        data["conflicts"] = conflicts
        if r_trunc:
            data["related_truncated"] = True
        if c_trunc:
            data["conflicts_truncated"] = True
    return {"status": "success", "data": data}


def _as_int(value: Any, *, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


_MAX_EDGES_PER_BUCKET = 20
_RELATED_RELS = ("supersedes", "cites", "distilled_from", "implements")
_CONFLICT_RELS = ("conflicts",)
# review S2：截断前按优先级排序，冲突侧优先呈现"最新/最强关联"
_REL_PRIORITY = {
    "supersedes": 0,
    "implements": 1,
    "distilled_from": 2,
    "cites": 3,
    "conflicts": 0,  # conflicts 桶单独排，值仅占位
}


def _related_edges(
    store: Any,
    item_ids: list[str],
    doc_index: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, bool]:
    """把 item_ids 在图谱里相连的边摘出来，返回 (related, conflicts, related_truncated, conflicts_truncated)。

    健壮性/一致性（review I3/S2/S5）：
    - 端点解析走 `doc_index`（由 handle 一次性预载 active 正式记忆）；
      不再逐边 store.read，避免 _sync_status_to_disk 写副作用与重复解析
    - 端点不在 doc_index → 视为 pending/archived/已删 → 忽略该边
    - (rel, anchor, other) 三元组去重
    - is_blocked(title+description) 过滤，与 rule_injector 的 catalog/tree 口径对齐
    - 截断前先按 _REL_PRIORITY 排序，冲突/取代类优先保留；截断回传 truncated 标记
    """
    if not item_ids or store is None or not doc_index:
        return [], [], False, False
    id_set = {str(i) for i in item_ids if i}
    edges: list[dict[str, Any]] = []
    try:
        from ...memory.graph import load_catalog

        rsi_dir = getattr(store, "rsi_dir", None)
        if rsi_dir is not None:
            edges.extend(load_catalog(rsi_dir).get("edges") or [])
    except Exception:  # noqa: BLE001 - 图谱层加载失败仍继续用 refs 合成的 cites
        pass
    # cites 边从 doc_index 里的 refs 现取（不再扫全库 list_all）
    for doc in doc_index.values():
        for ref in doc.refs or []:
            if ref:
                edges.append({"from": doc.id, "to": str(ref), "rel": "cites"})

    try:
        from ...injector.blocklist import is_blocked
    except Exception:  # noqa: BLE001 - 兜底：拿不到黑名单函数则跳过过滤
        def is_blocked(_text: str) -> bool:  # type: ignore[no-redef]
            return False

    seen: set[tuple[str, str, str]] = set()
    related: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        rel = str(edge.get("rel") or "")
        if rel in _CONFLICT_RELS:
            bucket = conflicts
        elif rel in _RELATED_RELS:
            bucket = related
        else:
            continue
        frm = str(edge.get("from") or "")
        to = str(edge.get("to") or "")
        if frm in id_set:
            anchor, other, direction = frm, to, "out"
        elif to in id_set:
            anchor, other, direction = to, frm, "in"
        else:
            continue
        if not other:
            continue
        key = (rel, anchor, other)
        if key in seen:
            continue
        seen.add(key)
        doc = doc_index.get(other)
        if doc is None:
            # 端点非 active（pending/archived/已删）：忽略该边
            continue
        title = str(getattr(doc, "title", "") or "")
        summary = str(getattr(doc, "description", "") or "")
        if is_blocked(f"{title}\n{summary}"):
            continue
        bucket.append({
            "rel": rel,
            "direction": direction,
            "anchor_id": anchor,
            "id": other,
            "title": title,
            "type": getattr(doc, "type", ""),
            "domain": getattr(doc, "domain", None),
            "status": "active",
        })
    # 截断前按优先级排序；同优先级按 other id 保证稳定
    def _sort(item: dict[str, Any]) -> tuple[int, str]:
        return (_REL_PRIORITY.get(item["rel"], 99), str(item.get("id") or ""))

    related.sort(key=_sort)
    conflicts.sort(key=_sort)
    r_trunc = len(related) > _MAX_EDGES_PER_BUCKET
    c_trunc = len(conflicts) > _MAX_EDGES_PER_BUCKET
    return (
        related[:_MAX_EDGES_PER_BUCKET],
        conflicts[:_MAX_EDGES_PER_BUCKET],
        r_trunc,
        c_trunc,
    )
