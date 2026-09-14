"""注入载体（Spec v3.0 §4.2）：RuleTarget 协议 + Cursor 规则文件 + AGENTS.md 托管块。

约定：
- 只写 rsi- 前缀文件与托管块内内容，绝不增量编辑用户手写内容；
- 全量幂等重写：每次以记忆库当前状态重建全部产物，陈旧 rsi-* 文件删除；
- 容量上限：单文件 ≤ 4000 字符，项目规则总量 ≤ 32KB（超出裁剪 convention，记日志）。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)

MAX_FILE_CHARS = 4000
MAX_TOTAL_CHARS = 32 * 1024
MAX_DECISIONS_CHARS = 800

#: 常驻抉择纪律（不注入冲突条目正文）
_DECISIONS_BODY = (
    "有 decisions 时由你调用 rsi_conflicts / rsi_knowledge_review 落库。"
    "用户要自动执行（你看着办/按推荐）时立刻用 recommended 调工具。"
    "用户要展开或问影响面时用 explain 或卡上的 sides/impact，不要关闭这张卡。"
    "不要让用户自己去终端跑 rsi。"
    "用户说重新学习时你执行 rsi bootstrap --consent --host-judge（要清文件记忆先 rsi wipe --yes），"
    "看到「必须指定 --host-judge 或 --local-judge」就加 --host-judge 重跑，不要改用 --local-judge。"
    "学完用 rsi_conflicts（可选带 bootstrap_run_id）处理未决冲突，"
    "每批最多 200 条 resolve，直到未决为 0；只有你不确定的才留给以后的 recall 卡。"
)

#: domain → globs 映射（宿主按 globs 挂载；未映射领域为空 = 通用经验，靠 description 触发）
_DOMAIN_GLOBS: Dict[str, str] = {
    "database": "**/*.sql",
    "frontend": "**/*.{ts,tsx,js,jsx,vue}",
    "backend": "**/*.{py,java,go,rs}",
    "test": "**/{test_*,*_test,*.spec}.*",
}


@dataclass
class MemoryRow:
    """待注入的记忆条目（knowledge_items 行的最小投影）"""

    id: str
    title: str
    content: str
    content_type: str
    domain: Optional[str] = None


@dataclass
class MemoryBundle:
    prohibitions: List[MemoryRow] = field(default_factory=list)
    conventions: List[MemoryRow] = field(default_factory=list)  # 含 legacy 'experience'
    skills: List[MemoryRow] = field(default_factory=list)  # name/description/path only


@dataclass
class Artifact:
    """一次重写产出的单个文件"""

    target: str           # cursor_rule | agents_md
    rel_path: str         # 相对项目根
    content_hash: str
    source_item_id: Optional[str]  # 聚合文件为 None


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _mdc(description: str, globs: str, always_apply: bool, body: str) -> str:
    return (
        f"---\ndescription: {description}\nglobs: {globs}\n"
        f"alwaysApply: {'true' if always_apply else 'false'}\n---\n\n{body}\n"
    )


class RuleTarget(Protocol):
    """载体适配器协议（多宿主扩展点，M2：.claude/skills 等）"""

    name: str

    def write(self, bundle: MemoryBundle) -> List[Artifact]:
        """以 bundle 全量重写本载体产物，返回产出清单（含已删除文件的移除语义由实现保证）"""
        ...


class CursorRuleTarget:
    """`.cursor/rules/rsi-*.mdc`：prohibition 一条一文件（alwaysApply），convention 按 domain 聚合"""

    name = "cursor_rule"

    def __init__(self, project_root: Path):
        self._rules_dir = Path(project_root) / ".cursor" / "rules"

    def write(self, bundle: MemoryBundle) -> List[Artifact]:
        self._rules_dir.mkdir(parents=True, exist_ok=True)
        produced: Dict[str, tuple[str, Optional[str]]] = {}  # filename → (content, source_item_id)
        total = 0

        decisions_body = _DECISIONS_BODY[:MAX_DECISIONS_CHARS]
        decisions_content = _mdc("RSI 对话内抉择纪律", "", True, decisions_body)
        produced["rsi-decisions.mdc"] = (decisions_content, None)
        total += len(decisions_content)

        for row in bundle.prohibitions:
            body = row.content[:MAX_FILE_CHARS]
            content = _mdc(row.title, "", True, body)
            produced[f"rsi-prohibition-{row.id[:8]}.mdc"] = (content, row.id)
            total += len(content)

        by_domain: Dict[str, List[MemoryRow]] = {}
        for row in bundle.conventions:
            by_domain.setdefault(row.domain or "general", []).append(row)
        for domain, rows in sorted(by_domain.items()):
            titles = "、".join(r.title for r in rows)[:120]
            body = "\n\n".join(f"## {r.title}\n{r.content}" for r in rows)
            content = _mdc(f"项目经验约定：{titles}", _DOMAIN_GLOBS.get(domain, ""), False, body)
            if len(content) > MAX_FILE_CHARS:
                content = content[:MAX_FILE_CHARS] + "\n\n<!-- 超出单文件上限，已裁剪 -->"
            if total + len(content) > MAX_TOTAL_CHARS:
                # 总量超限：裁剪最低优先的 convention 聚合（prohibition 不裁剪），事件记日志
                logger.warning("规则总量超 32KB，裁剪 convention 聚合 domain=%s", domain)
                continue
            produced[f"rsi-convention-{domain}.mdc"] = (content, None)
            total += len(content)

        # 全量重写语义：删除本次未再产出的 rsi-* 文件（用户手写规则不此前缀，不受影响）
        for stale in self._rules_dir.glob("rsi-*.mdc"):
            if stale.name not in produced:
                stale.unlink()
                logger.info("移除陈旧规则文件 %s", stale.name)

        artifacts: List[Artifact] = []
        for filename, (content, source_id) in produced.items():
            (self._rules_dir / filename).write_text(content, encoding="utf-8")
            artifacts.append(Artifact(
                target=self.name, rel_path=f".cursor/rules/{filename}",
                content_hash=_hash(content), source_item_id=source_id,
            ))
        return artifacts


#: 用户手写规则文件（相对项目根）：种子提取（rule_seed_scanner）与冲突检测
#:（injector.conflict）共用的发现集合；rsi-boot 只写 rsi-*.mdc 与 AGENTS.md
#: 托管块，以下文件均为用户自留地
_USER_RULE_REL_PATHS = (
    ".cursorrules",
    "AGENTS.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
)


def discover_user_rule_files(root: Path) -> List[Path]:
    """发现用户规则文件：.cursor/rules/*.mdc（非 rsi- 前缀）+ 各宿主约定文件"""
    root = Path(root)
    candidates: List[Path] = []
    rules_dir = root / ".cursor" / "rules"
    if rules_dir.is_dir():
        candidates += sorted(
            p for p in rules_dir.glob("*.mdc") if not p.name.startswith("rsi-")
        )
    for rel in _USER_RULE_REL_PATHS:
        path = root / rel
        if path.is_file():
            candidates.append(path)
    return candidates


class AgentsMdTarget:
    """AGENTS.md 托管块：仅重写 begin/end 标记之间，块外内容不动；无文件则创建"""

    name = "agents_md"
    BEGIN = "<!-- rsi-boot:begin -->"
    END = "<!-- rsi-boot:end -->"

    def __init__(self, project_root: Path):
        self._path = Path(project_root) / "AGENTS.md"

    def _render_block(self, bundle: MemoryBundle) -> str:
        lines = [self.BEGIN, "", "> 本块由 RSI Boot 自动维护，请勿手改（改动会被下次重写覆盖）。", ""]
        if bundle.prohibitions:
            lines.append("## 禁止项（必须遵守）")
            lines += [f"- **{r.title}**：{r.content}" for r in bundle.prohibitions]
            lines.append("")
        if bundle.conventions:
            lines.append("## 经验约定")
            lines += [f"- **{r.title}**：{r.content}" for r in bundle.conventions]
            lines.append("")
        if bundle.skills:
            lines.append("## 技能目录")
            for r in bundle.skills:
                path = r.domain or ""
                lines.append(f"- **{r.title}**：{r.content}（{path}）")
            lines.append("")
        lines.append(self.END)
        block = "\n".join(lines)
        if len(block) > MAX_TOTAL_CHARS:
            block = block[:MAX_TOTAL_CHARS] + "\n\n<!-- 超出上限，已裁剪 -->\n" + self.END
        return block

    def write(self, bundle: MemoryBundle) -> List[Artifact]:
        block = self._render_block(bundle)
        existing = self._path.read_text(encoding="utf-8") if self._path.is_file() else ""
        if self.BEGIN in existing and self.END in existing:
            head, _, rest = existing.partition(self.BEGIN)
            _, _, tail = rest.partition(self.END)
            content = head + block + tail
        else:
            content = (existing.rstrip() + "\n\n" if existing.strip() else "") + block + "\n"
        self._path.write_text(content, encoding="utf-8")
        return [Artifact(target=self.name, rel_path="AGENTS.md",
                         content_hash=_hash(content), source_item_id=None)]
