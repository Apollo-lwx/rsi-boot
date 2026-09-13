"""用户规则冲突检测（Spec v3.1 §4.9）：学习记忆 vs 用户手写规则。

扫描范围（只读，绝不修改用户文件）：非 rsi- 前缀的 .cursor/rules/*.mdc、
遗留 .cursorrules、AGENTS.md 托管块外内容、CLAUDE.md、.github/copilot-instructions.md。
零 Key 启发式产出「疑似」级冲突，裁决权在用户（rsi_conflicts resolve 三选一）。
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..scanner.conflict_gate import ConflictDraft

from ..core.masking import mask_text
from ..data.sqlite import SQLiteClient
from ..scanner.version_conflict import apply_conversation_hint, detect_version_families
from ..services.decision_queue import _peer_row
from .targets import AgentsMdTarget, discover_user_rule_files

logger = logging.getLogger(__name__)

_STALE_MTIME_DAYS = 90        # 用户规则文件超过该天数未更新才参与 stale 判定
_STALE_ADOPTION_MIN = 3       # 学习记忆近 30 天被采纳次数下限
_ADOPTION_WINDOW_DAYS = 30
_EXCERPT_WINDOW = 50          # 冲突摘录：命中点 ±50 字符
_EXCERPT_MAX = 500

_PROHIBITIVE = ("禁止", "不要", "不得", "避免", "严禁", "一律不",
                "never", "don't", "do not", "avoid", "must not")
_PERMISSIVE = ("允许", "可以", "建议", "使用", "优先", "推荐",
               "always", "prefer", "feel free", "may")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_tags(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if not raw:
        return []
    import json as _json
    try:
        data = _json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


@dataclass
class UserRuleFile:
    rel_path: str
    text: str
    content_hash: str
    mtime: float


class ConflictDetector:
    def __init__(
        self,
        db: SQLiteClient,
        project_root: Optional[Path] = None,
        on_change: Optional[Any] = None,
        knowledge: Optional[Any] = None,
    ):
        self._db = db
        self._root = Path(project_root) if project_root else None
        self._on_change = on_change  # user_wins 置 suppressed 后触发注入重写
        self._knowledge = knowledge

    # ---------- 扫描 ----------

    def _collect_user_rules(self) -> List[UserRuleFile]:
        assert self._root is not None
        files: List[UserRuleFile] = []
        for path in discover_user_rule_files(self._root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if path.name == "AGENTS.md":
                text = self._strip_managed_block(text)
                if not text.strip():
                    continue
            rel = path.relative_to(self._root).as_posix()
            files.append(UserRuleFile(
                rel_path=rel, text=text,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                mtime=path.stat().st_mtime,
            ))
        return files

    @staticmethod
    def _strip_managed_block(text: str) -> str:
        """AGENTS.md 托管块内是 rsi 自产内容，不参与冲突判定"""
        pattern = re.escape(AgentsMdTarget.BEGIN) + r".*?" + re.escape(AgentsMdTarget.END)
        return re.sub(pattern, "", text, flags=re.DOTALL)

    @staticmethod
    def _key_phrase(title: str) -> str:
        return title.removeprefix("禁止：").removeprefix("禁止:").strip()

    @staticmethod
    def _find_phrase(text: str, phrase: str) -> int:
        """定位 key phrase。ASCII 短语按词边界匹配（\\b，仅当首/尾字符为词字符时施加），
        避免 Executor 命中 ThreadPoolExecutor 内部子串；CJK 短语维持子串匹配"""
        if phrase.isascii():
            pattern = re.escape(phrase)
            if phrase[0].isalnum() or phrase[0] == "_":
                pattern = r"\b" + pattern
            if phrase[-1].isalnum() or phrase[-1] == "_":
                pattern += r"\b"
            match = re.search(pattern, text, re.IGNORECASE)
            return match.start() if match else -1
        return text.lower().find(phrase.lower())

    @staticmethod
    def _polarity(window: str) -> str:
        low = window.lower()
        if any(w in low for w in _PROHIBITIVE):
            return "prohibitive"
        if any(w in low for w in _PERMISSIVE):
            return "permissive"
        return "neutral"

    async def _recent_adoption(self, project_id: str, domain: Optional[str], tags: List[str]) -> int:
        """近 30 天 recall 命中标签含该记忆 domain/tags 的次数（stale 判定的采纳近似值）"""
        markers = [m for m in [domain, *tags] if m]
        if not markers:
            return 0
        conn = await self._db.connect()
        since = (datetime.now(timezone.utc) - timedelta(days=_ADOPTION_WINDOW_DAYS)).isoformat()
        total = 0
        for marker in markers[:3]:
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM interaction_logs WHERE project_id = ? AND intent = 'recall'"
                " AND created_at >= ? AND retrieved_tags LIKE ?",
                (project_id, since, f'%"{marker}"%'),
            ) as cur:
                total += int((await cur.fetchone())["n"])
        return total

    async def scan(self, project_id: str) -> Dict[str, int]:
        """全量扫描：新冲突 INSERT OR IGNORE 落库；memory_wins 行用户文件已变更则关闭"""
        if self._root is None:
            return {"scanned": 0, "detected": 0, "closed": 0, "version_detected": 0}
        files = self._collect_user_rules()
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT id, title, content_type, domain, tags FROM knowledge_items "
            "WHERE project_id = ? AND status = 'active' "
            "AND content_type IN ('prohibition', 'convention', 'experience')",
            (project_id,),
        ) as cur:
            memories = await cur.fetchall()

        detected = 0
        now = _utc_iso()
        for mem in memories:
            phrase = self._key_phrase(mem["title"])
            if len(phrase) < 2:
                continue
            import json as _json
            tags = _json.loads(mem["tags"] or "[]")
            for f in files:
                idx = self._find_phrase(f.text, phrase)
                if idx < 0:
                    continue
                window = f.text[max(0, idx - _EXCERPT_WINDOW): idx + len(phrase) + _EXCERPT_WINDOW]
                polarity = self._polarity(window)
                conflict_type: Optional[str] = None
                if mem["content_type"] == "prohibition":
                    # 窗口含禁止词 = 方向一致（无冲突）；仅当窗口存在许可词才疑似矛盾
                    #（中性窗口如「统一 X 而非 Y」方向一致，不报）
                    if polarity == "permissive":
                        conflict_type = "contradiction"
                else:
                    # convention/experience 命中即疑似重复；overlap 优先于 stale（互斥固化）
                    conflict_type = "overlap"
                if conflict_type is None and self._is_stale(f):
                    adoption = await self._recent_adoption(project_id, mem["domain"], tags)
                    if adoption >= _STALE_ADOPTION_MIN:
                        conflict_type = "stale"
                if conflict_type is None:
                    continue
                cur = await conn.execute(
                    "INSERT OR IGNORE INTO rule_conflicts"
                    " (id, project_id, item_id, user_rule_path, user_rule_excerpt,"
                    "  user_rule_hash, conflict_type, status, detected_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                    (uuid.uuid4().hex, project_id, mem["id"], f.rel_path,
                     mask_text(window.strip())[:_EXCERPT_MAX], f.content_hash,
                     conflict_type, now),
                )
                detected += cur.rowcount

        version_detected = await self._scan_version_families(project_id, conn, now)
        detected += version_detected

        closed = await self._close_resolved(project_id, files)
        await conn.commit()
        if detected or closed:
            logger.info("冲突扫描（项目 %s）：新增 %d，关闭 %d", project_id, detected, closed)
        return {
            "scanned": len(files), "detected": detected, "closed": closed,
            "version_detected": version_detected,
        }

    async def _recent_logs(self, project_id: str) -> List[Dict[str, Any]]:
        conn = await self._db.connect()
        since = (datetime.now(timezone.utc) - timedelta(days=_ADOPTION_WINDOW_DAYS)).isoformat()
        async with conn.execute(
            "SELECT raw_input, retrieved_tags FROM interaction_logs "
            "WHERE project_id = ? AND created_at >= ?",
            (project_id, since),
        ) as cur:
            rows = await cur.fetchall()
        logs: List[Dict[str, Any]] = []
        import json as _json
        for row in rows:
            tags = row["retrieved_tags"]
            try:
                tags = _json.loads(tags) if tags else []
            except (TypeError, ValueError):
                tags = []
            logs.append({"raw_input": row["raw_input"] or "", "retrieved_tags": tags})
        return logs

    @staticmethod
    def _norm_src(path: Optional[str]) -> str:
        return (path or "").replace("\\", "/")

    async def _scan_version_families(self, project_id: str, conn: Any, now: str) -> int:
        """文档多版本并排 → version 冲突（不改知识状态，交用户裁决）"""
        if self._root is None:
            return 0
        families = detect_version_families(self._root)
        if not families:
            return 0
        logs = await self._recent_logs(project_id)
        async with conn.execute(
            "SELECT id, source_url FROM knowledge_items "
            "WHERE project_id = ? AND source_url IS NOT NULL "
            "AND status IN ('active', 'pending_review', 'archived') "
            "ORDER BY id",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
        by_src: Dict[str, List[str]] = {}
        for row in rows:
            by_src.setdefault(self._norm_src(row["source_url"]), []).append(row["id"])

        async with conn.execute(
            "SELECT c.user_rule_path, c.user_rule_hash, k.source_url AS item_source "
            "FROM rule_conflicts c LEFT JOIN knowledge_items k ON k.id = c.item_id "
            "WHERE c.project_id = ? AND c.conflict_type = 'version'",
            (project_id,),
        ) as cur:
            existing = await cur.fetchall()
        seen_hashes = {r["user_rule_hash"] for r in existing}
        seen_pairs = {
            frozenset((self._norm_src(r["item_source"]), self._norm_src(r["user_rule_path"])))
            for r in existing
        }

        detected = 0
        for raw in families:
            family = apply_conversation_hint(raw, logs)
            legacy_ids = by_src.get(family.legacy_rel, [])
            current_ids = by_src.get(family.current_rel, [])
            if not legacy_ids or not current_ids:
                continue
            item_id = legacy_ids[0]
            excerpt = mask_text(
                f"[version/{family.kind}] 倾向保留 {family.current_rel}（{family.reason}）\n"
                f"另一版: {family.legacy_rel}\n"
                f"裁决: keep_peer=接受倾向（归档另一版） "
                f"keep_item=推翻倾向（归档倾向版） coexist=两版都留"
            )[:_EXCERPT_MAX]
            a, b = sorted((family.legacy_rel, family.current_rel))
            family_hash = hashlib.sha256(f"version:{a}:{b}".encode("utf-8")).hexdigest()
            pair = frozenset((family.legacy_rel, family.current_rel))
            if family_hash in seen_hashes or pair in seen_pairs:
                continue
            cur = await conn.execute(
                "INSERT OR IGNORE INTO rule_conflicts"
                " (id, project_id, item_id, user_rule_path, user_rule_excerpt,"
                "  user_rule_hash, conflict_type, status, detected_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'version', 'open', ?)",
                (uuid.uuid4().hex, project_id, item_id, family.current_rel,
                 excerpt, family_hash, now),
            )
            if cur.rowcount:
                seen_hashes.add(family_hash)
                seen_pairs.add(pair)
            detected += cur.rowcount
        return detected

    @staticmethod
    def _is_stale(f: UserRuleFile) -> bool:
        cutoff = datetime.now(timezone.utc).timestamp() - _STALE_MTIME_DAYS * 86400
        return f.mtime < cutoff

    async def _close_resolved(self, project_id: str, files: List[UserRuleFile]) -> int:
        """memory_wins 裁决后，用户文件 hash 变化（用户已手动修改）→ 冲突关闭"""
        by_path = {f.rel_path: f for f in files}
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT id, user_rule_path, user_rule_hash FROM rule_conflicts "
            "WHERE project_id = ? AND status = 'memory_wins'",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
        closed = 0
        for row in rows:
            current = by_path.get(row["user_rule_path"])
            # 文件被删除或内容已变更均视为用户已处理
            if current is None or current.content_hash != row["user_rule_hash"]:
                await conn.execute(
                    "UPDATE rule_conflicts SET status = 'closed', resolved_at = ? WHERE id = ?",
                    (_utc_iso(), row["id"]),
                )
                closed += 1
        return closed

    # ---------- 查询与裁决 ----------

    async def list_conflicts(
        self, project_id: str, status: str = "open",
        *, bootstrap_run_id: Optional[str] = None,
        limit: int = 100, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        conn = await self._db.connect()
        sql = (
            "SELECT c.id, c.item_id, k.title AS item_title, k.source_url AS item_source,"
            " c.user_rule_path, c.user_rule_excerpt,"
            " c.conflict_type, c.status, c.detected_at, c.resolved_at, c.resolution_note"
            " FROM rule_conflicts c LEFT JOIN knowledge_items k ON k.id = c.item_id"
            " WHERE c.project_id = ?"
        )
        args: tuple = (project_id,)
        if status != "all":
            sql += " AND c.status = ?"
            args = (project_id, status)
        async with conn.execute(sql + " ORDER BY c.detected_at DESC, c.id DESC", args) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        if bootstrap_run_id:
            marker = f"bootstrap_run_id:{bootstrap_run_id}"
            rows = [r for r in rows if await self._in_run(conn, project_id, r, marker)]
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        return rows[offset:offset + limit]

    async def _in_run(
        self, conn: Any, project_id: str, row: Dict[str, Any], marker: str,
    ) -> bool:
        """run 归属（与 knowledge_accept._conflicts_for_run 同口径）：
        item_id 侧 tags 含 marker，或 user_rule_path 侧（_peer_row 解析）条目 tags 含 marker"""
        item_id = row.get("item_id")
        if item_id:
            async with conn.execute(
                "SELECT tags FROM knowledge_items WHERE id = ?", (item_id,),
            ) as cur:
                item = await cur.fetchone()
            if item and marker in _parse_tags(item["tags"]):
                return True
        peer_src = self._norm_src(row.get("user_rule_path"))
        if not peer_src:
            return False
        peer = await _peer_row(conn, project_id, peer_src, row.get("user_rule_excerpt") or "")
        return bool(peer and marker in _parse_tags(peer.get("tags")))

    async def persist_knowledge_conflicts(
        self, project_id: str, drafts: list[ConflictDraft],
        source_to_item_id: dict[str, str],
    ) -> int:
        """INSERT OR IGNORE；item_id = left_source 对应条目；user_rule_path = right_source。"""
        mapped = {self._norm_src(k): v for k, v in source_to_item_id.items()}
        conn = await self._db.connect()
        now = _utc_iso()
        inserted = 0
        for draft in drafts:
            left = self._norm_src(draft.left_source)
            right = self._norm_src(draft.right_source)
            item_id = mapped.get(left)
            if not item_id or not right:
                continue
            a, b = sorted((left, right))
            family_hash = hashlib.sha256(
                f"{draft.conflict_type}:{a}:{b}".encode("utf-8")
            ).hexdigest()
            excerpt = mask_text(
                f"[{draft.conflict_type}] {draft.reason}\n"
                f"倾向: {draft.recommended}（{draft.recommended_reason}）\n"
                f"另一侧: {right}"
            )[:_EXCERPT_MAX]
            cur = await conn.execute(
                "INSERT OR IGNORE INTO rule_conflicts"
                " (id, project_id, item_id, user_rule_path, user_rule_excerpt,"
                "  user_rule_hash, conflict_type, status, resolution_note, detected_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (uuid.uuid4().hex, project_id, item_id, right, excerpt, family_hash,
                 draft.conflict_type, f"recommended:{draft.recommended}", now),
            )
            inserted += cur.rowcount
        await conn.commit()
        return inserted

    async def explain(self, conflict_id: str) -> Optional[Dict[str, Any]]:
        """只读。返回 sides、impact、options，不 UPDATE。"""
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM rule_conflicts WHERE id = ?", (conflict_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        item = await self._load_item(conn, row["item_id"])
        peer_src = self._norm_src(row["user_rule_path"])
        peer = await _peer_row(
            conn, row["project_id"],
            peer_src,
            row["user_rule_excerpt"] or "",
        )
        item_excerpt = mask_text((item or {}).get("content") or "")[:_EXCERPT_MAX]
        peer_excerpt = row["user_rule_excerpt"] or ""
        if peer:
            peer_excerpt = mask_text(peer.get("content") or peer_excerpt)[:_EXCERPT_MAX]
        sides = [
            {
                "role": "item",
                "item_id": row["item_id"],
                "title": (item or {}).get("title") or "",
                "excerpt": item_excerpt,
                "source": self._norm_src((item or {}).get("source_url") or ""),
                "status": (item or {}).get("status") or "",
            },
            {
                "role": "peer",
                "item_id": peer["id"] if peer else None,
                "title": (peer or {}).get("title") or "",
                "excerpt": peer_excerpt,
                "source": peer_src,
                "status": (peer or {}).get("status") or "",
            },
        ]
        recommended = self._recommended_of(row)
        options = self._explain_options(row["conflict_type"], sides)
        return {
            "id": conflict_id,
            "conflict_type": row["conflict_type"],
            "status": row["status"],
            "sides": sides,
            "impact": {
                "recall": "之后相关任务会按你选的那条注入；归档侧不再进入召回",
                "inject": "规则文件 rsi-*.mdc / AGENTS.md 会按保留侧重写",
                "code_hint": peer_src if row["conflict_type"] == "doc_code" else "",
            },
            "options": options,
            "recommended": recommended,
            "recommended_reason": row["user_rule_excerpt"] or "",
        }

    @staticmethod
    def _recommended_of(row: Any) -> str:
        note = row["resolution_note"] or ""
        if note.startswith("recommended:"):
            return note.split(":", 1)[1].strip().split()[0]
        return ""

    @staticmethod
    def _explain_options(ctype: str, sides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        item_src = sides[0]["source"] or "item 侧"
        peer_src = sides[1]["source"] or "对侧"
        if ctype in ("version", "doc_code", "incoherent"):
            return [
                {
                    "id": "keep_item", "resolution": "keep_item",
                    "label": f"保留 {item_src}，归档 {peer_src}",
                    "effect": f"归档 {peer_src} 的 pending_review/active；{item_src} 若待审则改为 active",
                },
                {
                    "id": "keep_peer", "resolution": "keep_peer",
                    "label": f"保留 {peer_src}，归档 {item_src}",
                    "effect": f"归档 {item_src} 的 pending_review/active；{peer_src} 若待审则改为 active",
                },
                {
                    "id": "coexist", "resolution": "coexist",
                    "label": "两版都留，以后按场景再看",
                    "effect": "两侧 pending→active，不归档，该组合不再提醒",
                },
            ]
        return [
            {"id": "user_wins", "resolution": "user_wins",
             "label": "以你的规则为准", "effect": "学习记忆置 suppressed，不再注入"},
            {"id": "memory_wins", "resolution": "memory_wins",
             "label": "以学习记忆为准", "effect": "请手动改规则文件，下次扫描关闭冲突"},
            {"id": "coexist", "resolution": "coexist",
             "label": "两者共存", "effect": "该组合不再提醒"},
        ]

    async def _load_item(self, conn: Any, item_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not item_id:
            return None
        async with conn.execute(
            "SELECT id, title, content, source_url, status FROM knowledge_items WHERE id = ?",
            (item_id,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def _items_by_source(
        self, conn: Any, project_id: str, source: str,
    ) -> List[Dict[str, Any]]:
        if not source:
            return []
        async with conn.execute(
            "SELECT id, title, content, source_url, status FROM knowledge_items "
            "WHERE project_id = ?",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows if self._norm_src(r["source_url"]) == source]

    async def resolve(
        self, conflict_id: str, resolution: str, note: str = ""
    ) -> Optional[Dict[str, Any]]:
        """裁决。规则冲突：user_wins/memory_wins/coexist；
        version/doc_code/incoherent：keep_peer/keep_item/coexist。不存在或已关闭返回 None"""
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT * FROM rule_conflicts WHERE id = ?", (conflict_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["status"] not in ("open",):
            return None

        ctype = row["conflict_type"]
        allowed = (
            ("keep_peer", "keep_item", "coexist")
            if ctype in ("version", "doc_code", "incoherent")
            else ("user_wins", "memory_wins", "coexist")
        )
        if resolution not in allowed:
            return None

        now = _utc_iso()
        guidance = ""
        activate_ids: List[str] = []
        if ctype == "version":
            guidance, activate_ids = await self._resolve_version(row, resolution, now)
        elif ctype in ("doc_code", "incoherent"):
            guidance, activate_ids = await self._resolve_knowledge_pair(row, resolution, now)
        elif resolution == "user_wins":
            # 学习记忆置 suppressed：保留在库、不再注入；后续同主题草稿审批时可见此前裁决
            await conn.execute(
                "UPDATE knowledge_items SET status = 'suppressed', updated_at = ? WHERE id = ?",
                (now, row["item_id"]),
            )
            guidance = "已按用户规则为准：该学习记忆不再注入宿主上下文（保留在库供审计）"
        elif resolution == "memory_wins":
            guidance = (f"请手动修改或删除你的规则文件 {row['user_rule_path']}；"
                        f"系统将在下次扫描检测到文件变更后自动关闭本冲突")
        else:
            guidance = "已标记两者共存，该组合不再提醒"
        await conn.execute(
            "UPDATE rule_conflicts SET status = ?, resolution_note = ?, resolved_at = ? WHERE id = ?",
            (resolution, note or None, now, conflict_id),
        )
        await conn.commit()
        notify = resolution in ("user_wins", "keep_peer", "keep_item", "coexist")
        if notify and self._knowledge is not None:
            try:
                await self._knowledge.finalize_active(row["project_id"], activate_ids)
            except Exception:
                logger.exception("裁决后补 embedding/注入失败（下轮变更重试）")
        elif notify and self._on_change is not None:
            try:
                await self._on_change(row["project_id"])
            except Exception:
                logger.exception("裁决后注入重写失败（下轮变更重试）")
        return {"conflict_id": conflict_id, "resolution": resolution, "guidance": guidance}

    async def resolve_batch(
        self, decisions: List[Dict[str, str]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """逐条调 self.resolve；返回 {"resolved": [...], "failed": [{conflict_id, reason}]}。
        部分失败不回滚已成功。"""
        resolved: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        for d in decisions:
            result = await self.resolve(
                d["conflict_id"], d["resolution"], d.get("note") or "",
            )
            if result is None:
                failed.append({
                    "conflict_id": d["conflict_id"],
                    "reason": "冲突不存在/已关闭，或 resolution 非法",
                })
            else:
                resolved.append(result)
        return {"resolved": resolved, "failed": failed}

    async def _resolve_version(self, row: Any, resolution: str, now: str) -> tuple[str, List[str]]:
        """版本冲突裁决：归档落败来源全部条目，保留侧 pending→active；coexist 两侧激活。"""
        conn = await self._db.connect()
        async with conn.execute(
            "SELECT id, source_url, status FROM knowledge_items WHERE id = ?",
            (row["item_id"],),
        ) as cur:
            item_row = await cur.fetchone()
        item_src = self._norm_src(item_row["source_url"] if item_row else "")
        peer_src = self._norm_src(row["user_rule_path"])
        async with conn.execute(
            "SELECT id, source_url, status FROM knowledge_items "
            "WHERE project_id = ? AND status IN ('active', 'pending_review')",
            (row["project_id"],),
        ) as cur:
            candidates = await cur.fetchall()

        def side(src: str) -> List[Any]:
            return [r for r in candidates if src and self._norm_src(r["source_url"]) == src]

        item_side = side(item_src)
        peer_side = side(peer_src)

        if resolution == "coexist":
            activate = [
                r["id"] for r in (*item_side, *peer_side) if r["status"] == "pending_review"
            ]
            await self._set_item_status(conn, activate, "active", now)
            return "已标记两版共存并转为可用，该组合不再提醒", activate

        if resolution == "keep_item":
            archive = [r["id"] for r in peer_side]
            activate = [r["id"] for r in item_side if r["status"] == "pending_review"]
            archive_src, keep_src = peer_src, item_src
        else:
            archive = [r["id"] for r in item_side]
            activate = [r["id"] for r in peer_side if r["status"] == "pending_review"]
            archive_src, keep_src = item_src, peer_src
        if not archive_src and resolution != "coexist":
            return "无法定位待归档来源，已记录裁决但未改知识状态", []
        await self._set_item_status(conn, archive, "archived", now)
        await self._set_item_status(conn, activate, "active", now)
        return (
            f"已按你的选择归档 {archive_src} 一侧，保留 {keep_src}（可在审批队列/知识列表核对）",
            activate,
        )

    async def _resolve_knowledge_pair(
        self, row: Any, resolution: str, now: str,
    ) -> tuple[str, List[str]]:
        """doc_code/incoherent：按合成对侧键定位单条；保留侧 pending→active；coexist 两侧激活。"""
        conn = await self._db.connect()
        item = await self._load_item(conn, row["item_id"])
        peer = await _peer_row(
            conn, row["project_id"],
            self._norm_src(row["user_rule_path"]),
            row["user_rule_excerpt"] or "",
        )
        item_src = self._norm_src((item or {}).get("source_url") or "")
        peer_src = self._norm_src(row["user_rule_path"])

        if resolution == "coexist":
            activate = [
                r["id"] for r in (item, peer)
                if r is not None and r.get("status") == "pending_review"
            ]
            await self._set_item_status(conn, activate, "active", now)
            return "已标记两者共存并转为可用，该组合不再提醒", activate

        if resolution == "keep_item":
            archive = [peer["id"]] if peer is not None and peer["id"] != row["item_id"] else []
            activate = (
                [item["id"]] if item is not None and item.get("status") == "pending_review" else []
            )
            keep_src, archive_src = item_src, peer_src
        else:
            archive = [item["id"]] if item is not None and (peer is None or item["id"] != peer["id"]) else []
            activate = (
                [peer["id"]] if peer is not None and peer.get("status") == "pending_review" else []
            )
            keep_src, archive_src = peer_src, item_src
        await self._set_item_status(conn, archive, "archived", now)
        await self._set_item_status(conn, activate, "active", now)
        return (
            f"已按你的选择归档 {archive_src} 一侧，保留 {keep_src}（可在审批队列/知识列表核对）",
            activate,
        )

    @staticmethod
    async def _set_item_status(conn: Any, ids: List[str], status: str, now: str) -> None:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            await conn.execute(
                f"UPDATE knowledge_items SET status = ?, updated_at = ? "
                f"WHERE id IN ({','.join('?' for _ in chunk)})",
                [status, now, *chunk],
            )
