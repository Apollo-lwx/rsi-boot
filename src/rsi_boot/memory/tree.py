"""层级知识目录（对标 Qoder knowledge_module_tree）：从 active 记忆的 domain 字段
聚合成可导航的模块树。

设计取舍（用户确认）：
- 数据源 = 复用现有 domain 字段（domain 常为 `top` 或 `top/sub`，按 `/` 分段即层级），
  零新采集、与既有「按 domain 聚合 convention」一致。
- 本模块只做「构建 + 查询 + 渲染」的纯函数，注入端（rule_injector/targets）与
  explore 工具（api/tools/explore_tool）共用同一棵树，避免两套语义。
- 简述只取显式 description，正文一律不外泄（详情走 rsi_recall）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .types import MemoryDoc

#: 无 domain 的记忆归入此桶，保证仍有被动可见的落点
UNDOMAINED = "general"
_BRIEF_CHARS = 80

#: 进入知识树的记忆类型（与扁平 catalog 一致；prohibition/skill 有各自注入通道）
TREE_TYPES = ("convention", "documentation", "teaching_case", "gene_case", "episode")


def domain_segments(doc: MemoryDoc) -> List[str]:
    """把 domain 拆成层级段；空 domain → [general]。兼容 `\\` 分隔与首尾斜杠。"""
    raw = str(doc.domain or "").strip().strip("/\\").replace("\\", "/")
    segs = [s.strip() for s in raw.split("/") if s.strip()]
    return segs or [UNDOMAINED]


def _brief(doc: MemoryDoc) -> str:
    return " ".join((doc.description or "").split())[:_BRIEF_CHARS]


@dataclass
class TreeNode:
    name: str                       # 段名（末段）
    path: str                       # 全路径（"/".join），根为 ""
    is_root: bool = False
    direct: int = 0                 # domain 恰好终止于本节点的条目数
    children: Dict[str, "TreeNode"] = field(default_factory=dict)
    items: List[Dict[str, str]] = field(default_factory=list)  # {id,title,brief,type}

    @property
    def total(self) -> int:
        """子树条目总数（含本节点直挂）。"""
        return self.direct + sum(c.total for c in self.children.values())

    def sorted_children(self) -> List["TreeNode"]:
        return sorted(self.children.values(), key=lambda c: (-c.total, c.name))

    def descendant_items(self, *, limit: int = 50) -> List[Dict[str, str]]:
        """深度优先收集本节点及子孙的条目，至多 limit 条（供 explore 下钻列出）。"""
        out: List[Dict[str, str]] = []

        def walk(node: "TreeNode") -> bool:
            for it in node.items:
                out.append(it)
                if len(out) >= limit:
                    return False
            for child in node.sorted_children():
                if not walk(child):
                    return False
            return True

        walk(self)
        return out


def build_tree(docs: Iterable[MemoryDoc]) -> TreeNode:
    """把 active 记忆按 domain 分段聚合成层级树。"""
    root = TreeNode(name="", path="", is_root=True)
    for doc in docs:
        if doc.status != "active":
            continue
        segs = domain_segments(doc)
        node = root
        for i, seg in enumerate(segs):
            child = node.children.get(seg)
            if child is None:
                child = TreeNode(name=seg, path="/".join(segs[: i + 1]))
                node.children[seg] = child
            node = child
        node.direct += 1
        node.items.append(
            {"id": doc.id, "title": doc.title, "brief": _brief(doc), "type": doc.type}
        )
    return root


def find_node(root: TreeNode, path: Optional[str]) -> Optional[TreeNode]:
    """按路径前缀定位节点；空/None → 根。未知路径返回 None。"""
    raw = str(path or "").strip().strip("/\\").replace("\\", "/")
    if not raw:
        return root
    node = root
    for seg in (s for s in raw.split("/") if s.strip()):
        node = node.children.get(seg.strip())
        if node is None:
            return None
    return node


def render_lines(root: TreeNode, *, max_depth: int = 2, max_nodes: int = 40) -> List[str]:
    """渲染成缩进目录行（节点名 + 子树计数），仅显示前 max_depth 层，节点数封顶。
    顶层节点附 rsi_explore 下钻提示；标题/简述清单由 catalog 或 explore 提供，此处不铺开。"""
    lines: List[str] = []
    budget = {"n": 0}

    def walk(node: TreeNode, level: int) -> None:
        for child in node.sorted_children():
            if budget["n"] >= max_nodes:
                return
            budget["n"] += 1
            indent = "  " * (level - 1)
            hint = f"  ·  rsi_explore \"{child.path}\"" if level == 1 else ""
            lines.append(f"{indent}- {child.name} ({child.total}){hint}")
            if level < max_depth:
                walk(child, level + 1)

    walk(root, 1)
    return lines


def load_tree(store, *, types: Iterable[str] = TREE_TYPES) -> TreeNode:
    """从 store 读取 active 正式记忆（skip_harvest）并按 domain 构树。
    注入端与 explore 工具共用，保证两端看到的板块结构一致。"""
    docs: List[MemoryDoc] = []
    for typ in types:
        for doc in store.list_official(typ, skip_harvest=True):
            if doc.status == "active":
                docs.append(doc)
    return build_tree(docs)
