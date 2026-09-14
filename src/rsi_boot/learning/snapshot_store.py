"""Genome 式整体快照（§3.9）：提案层存 diff，版本层存整体快照，回滚 = 切换快照。

- 每次提案晋升生效时，将 6 槽位当前内容整体导出写入 config_snapshots，
  版本号项目内单调递增
- 存储合并语义（与 §2.5 配置链一致）：槽位内容与上一版本解析结果一致时存
  {"inherit": true}（缺省 = 继承）；{"reset": true} 表示显式重置回默认（空槽）；
  否则存 {"value": <内容>}（有值 = 覆盖）——单槽位小改不复制全量内容
- 回滚 = 将目标历史快照标记 rolled_back_to 并以其解析内容重建槽位，
  原快照转 superseded；任意历史版本可切换
- 快照可导出为目录型 bundle（genome.json 清单 + 各槽位文件）

槽位载体：prompt_template/skill/intent_rule 为 ~/.rsi 下文件；strategy/knowledge/profile
为 YAML / MemoryStore。knowledge 快照不存 embedding，恢复时重新生成。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..memory.store import MemoryStore

logger = logging.getLogger(__name__)

SLOTS = ("prompt_template", "skill", "strategy", "intent_rule", "knowledge", "profile")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SnapshotStore:
    def __init__(
        self,
        store: MemoryStore,
        rsi_home: Optional[Path] = None,
    ):
        self._store = store
        self._home = rsi_home if rsi_home is not None else store.rsi_dir

    # ---------- 槽位采集与重建 ----------

    def _load_yaml_file(self, path: Path, key: str | None = None) -> Any:
        if not path.is_file():
            return [] if key else None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return [] if key else None
        if key is None:
            return data
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            rows = data.get(key) or data.get("items") or []
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
        return []

    def _file_overlays(self) -> Dict[str, Any]:
        templates: Dict[str, str] = {}
        tpl_dir = self._home / "templates"
        if tpl_dir.is_dir():
            for p in sorted(tpl_dir.glob("*.jinja2")):
                templates[p.name] = p.read_text(encoding="utf-8")

        skills: Dict[str, str] = {}
        skill_root = self._home / "skills"
        if skill_root.is_dir():
            for p in sorted(skill_root.glob("*/SKILL.md")):
                skills[p.parent.name] = p.read_text(encoding="utf-8")

        intent_overlay = ""
        overlay_path = self._home / "intent_rules.yaml"
        if overlay_path.is_file():
            intent_overlay = overlay_path.read_text(encoding="utf-8")
        return {
            "prompt_template": templates,
            "skill": skills,
            "intent_rule": {"overlay_yaml": intent_overlay},
        }

    def _gather_slots_store(self, project_id: str) -> Dict[str, Any]:
        overlays = self._file_overlays()
        strategy = self._load_yaml_file(self._home / "state" / "arms.yaml", "arms")
        knowledge = []
        for doc in [*self._store.list_official(), *self._store.list_pending()]:
            knowledge.append({
                "id": doc.id,
                "title": doc.title,
                "content": doc.content,
                "type": doc.type,
                "status": doc.status,
                "tags": list(doc.tags or []),
                "roles": list(doc.roles or []),
                "domain": doc.domain,
            })
        profile = self._load_yaml_file(self._home / "state" / "profile.yaml")
        if profile is None:
            profile = []
        return {
            "prompt_template": overlays["prompt_template"],
            "skill": overlays["skill"],
            "strategy": strategy,
            "intent_rule": overlays["intent_rule"],
            "knowledge": knowledge,
            "profile": profile,
        }

    def _restore_file_overlays(self, slots: Dict[str, Any]) -> None:
        tpl_dir = self._home / "templates"
        tpl_dir.mkdir(parents=True, exist_ok=True)
        for p in tpl_dir.glob("*.jinja2"):
            p.unlink()
        for name, content in (slots.get("prompt_template") or {}).items():
            (tpl_dir / name).write_text(content, encoding="utf-8")

        skill_root = self._home / "skills"
        skill_root.mkdir(parents=True, exist_ok=True)
        for d in skill_root.iterdir():
            if d.is_dir() and (d / "SKILL.md").is_file():
                (d / "SKILL.md").unlink()
                d.rmdir()
        for name, content in (slots.get("skill") or {}).items():
            d = skill_root / name
            d.mkdir(exist_ok=True)
            (d / "SKILL.md").write_text(content, encoding="utf-8")

        overlay = (slots.get("intent_rule") or {}).get("overlay_yaml", "")
        overlay_path = self._home / "intent_rules.yaml"
        if overlay:
            overlay_path.write_text(overlay, encoding="utf-8")
        elif overlay_path.is_file():
            overlay_path.unlink()

    def _restore_slots_store(self, project_id: str, slots: Dict[str, Any]) -> None:
        self._restore_file_overlays(slots)
        arms_path = self._home / "state" / "arms.yaml"
        arms_path.parent.mkdir(parents=True, exist_ok=True)
        rows = slots.get("strategy") or []
        tmp = Path(str(arms_path) + ".tmp")
        tmp.write_text(yaml.safe_dump({"arms": rows}, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(tmp, arms_path)

        from ..memory.paths import official_dir
        from ..memory.store import memory_filename
        from ..memory.types import MemoryDoc
        for row in slots.get("knowledge") or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            try:
                doc = MemoryDoc(
                    id=row["id"],
                    type=row.get("type") or "documentation",
                    title=row.get("title") or row["id"],
                    content=row.get("content") or "",
                    tags=list(row.get("tags") or []),
                    roles=list(row.get("roles") or []),
                    domain=row.get("domain"),
                )
                dest = official_dir(self._store.rsi_dir, doc.type) / memory_filename(doc.title, doc.id)
                self._store.write(doc, dest=dest)
            except (ValueError, TypeError, KeyError):
                continue

        profile = slots.get("profile")
        if profile is not None:
            profile_path = self._home / "state" / "profile.yaml"
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = Path(str(profile_path) + ".tmp")
            tmp.write_text(yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8")
            os.replace(tmp, profile_path)

    def _resolve_store(self, project_id: str, version: int) -> Dict[str, Any]:
        docs = [
            d for d in self._iter_snapshot_docs(project_id)
            if int(d.get("version") or 0) <= version
        ]
        docs.sort(key=lambda d: int(d.get("version") or 0))
        resolved: Dict[str, Any] = {}
        for data in docs:
            slots = data.get("slots") or {}
            if isinstance(slots, str):
                try:
                    slots = json.loads(slots)
                except (TypeError, ValueError):
                    continue
            if not isinstance(slots, dict):
                continue
            for slot, entry in slots.items():
                if not isinstance(entry, dict):
                    resolved[slot] = entry
                    continue
                if entry.get("inherit"):
                    continue
                if entry.get("reset"):
                    resolved[slot] = {} if slot != "intent_rule" else {"overlay_yaml": ""}
                elif "value" in entry:
                    resolved[slot] = entry["value"]
        return resolved

    async def _gather_slots(self, project_id: str) -> Dict[str, Any]:
        """导出 6 槽位当前内容（文件槽读 ~/.rsi 覆写层；YAML / MemoryStore 读项目行）"""
        return self._gather_slots_store(project_id)

    async def _restore_slots(self, project_id: str, slots: Dict[str, Any]) -> None:
        """以解析后的槽位内容重建（回滚 = 切换快照，非逆向重放 diff）"""
        self._restore_slots_store(project_id, slots)

    # ---------- 版本管理 ----------

    async def _latest_version(self, project_id: str) -> int:
        versions = [v["version"] for v in self._list_store(project_id)]
        return max(versions) if versions else 0

    async def _resolve(self, project_id: str, version: int) -> Dict[str, Any]:
        """按合并语义解析指定版本的完整槽位内容（沿版本链回放 inherit/reset/value）"""
        return self._resolve_store(project_id, version)

    async def capture(self, project_id: str, trigger_proposal: Optional[str] = None) -> int:
        """晋升时整体快照：与上一版本解析结果一致的槽位存 inherit 标记。返回新版本号"""
        return self._capture_store(project_id, trigger_proposal)

    async def list(self, project_id: str) -> List[Dict[str, Any]]:
        return self._list_store(project_id)

    async def switch(self, project_id: str, version: int) -> bool:
        """回滚/切换：解析目标版本内容重建槽位；目标标记 rolled_back_to，原 active 转 superseded"""
        return self._switch_store(project_id, version)

    async def export_bundle(self, project_id: str, version: int, out_dir: Path) -> Optional[Path]:
        """导出目录型 bundle：genome.json 清单 + 各槽位文件（跨项目迁移/备份）"""
        versions = {v["version"] for v in await self.list(project_id)}
        if version not in versions:
            return None
        resolved = await self._resolve(project_id, version)
        out_dir.mkdir(parents=True, exist_ok=True)

        manifest: Dict[str, Any] = {"project_id": project_id, "version": version,
                                    "exported_at": _utc_iso(), "slots": {}}
        for slot, content in resolved.items():
            if slot == "prompt_template":
                d = out_dir / "templates"
                d.mkdir(exist_ok=True)
                for name, text in content.items():
                    (d / name).write_text(text, encoding="utf-8")
                manifest["slots"][slot] = sorted(content)
            elif slot == "skill":
                for name, text in content.items():
                    d = out_dir / "skills" / name
                    d.mkdir(parents=True, exist_ok=True)
                    (d / "SKILL.md").write_text(text, encoding="utf-8")
                manifest["slots"][slot] = sorted(content)
            elif slot == "intent_rule":
                (out_dir / "intent_rules.yaml").write_text(
                    content.get("overlay_yaml", ""), encoding="utf-8"
                )
                manifest["slots"][slot] = "intent_rules.yaml"
            else:
                (out_dir / f"{slot}.json").write_text(
                    json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                manifest["slots"][slot] = f"{slot}.json"
        (out_dir / "genome.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return out_dir

    def _snapshots_root(self) -> Path:
        return self._store.rsi_dir / "state" / "snapshots"

    def _list_store(self, project_id: str) -> List[Dict[str, Any]]:
        root = self._snapshots_root()
        if not root.is_dir():
            return []
        rows: List[Dict[str, Any]] = []
        for snap in sorted(root.glob("*/snapshot.yaml")):
            try:
                data = yaml.safe_load(snap.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(data, dict):
                continue
            if project_id and data.get("project_id") and data.get("project_id") != project_id:
                continue
            rows.append({
                "version": data.get("version"),
                "trigger_proposal": data.get("trigger_proposal"),
                "status": data.get("status"),
                "created_at": data.get("created_at"),
            })
        rows.sort(key=lambda r: int(r["version"] or 0), reverse=True)
        return rows

    def _write_snapshot_yaml(self, data: Dict[str, Any]) -> None:
        ver = data.get("version")
        dest = self._snapshots_root() / str(ver) / "snapshot.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(dest) + ".tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(tmp, dest)

    def _iter_snapshot_docs(self, project_id: str) -> List[Dict[str, Any]]:
        root = self._snapshots_root()
        if not root.is_dir():
            return []
        docs: List[Dict[str, Any]] = []
        for snap in sorted(root.glob("*/snapshot.yaml")):
            try:
                data = yaml.safe_load(snap.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(data, dict):
                continue
            if project_id and data.get("project_id") and data.get("project_id") != project_id:
                continue
            docs.append(data)
        return docs

    def _capture_store(self, project_id: str, trigger_proposal: Optional[str] = None) -> int:
        current = self._gather_slots_store(project_id)
        version = 1
        for data in self._iter_snapshot_docs(project_id):
            ver = int(data.get("version") or 0)
            if ver >= version:
                version = ver + 1
            if data.get("status") == "active":
                data["status"] = "superseded"
                self._write_snapshot_yaml(data)
        previous = self._resolve_store(project_id, version - 1) if version > 1 else {}
        slots: Dict[str, Any] = {}
        for slot, content in current.items():
            if previous.get(slot) == content:
                slots[slot] = {"inherit": True}
            else:
                slots[slot] = {"value": content}
        payload = {
            "id": uuid.uuid4().hex,
            "project_id": project_id,
            "version": version,
            "trigger_proposal": trigger_proposal,
            "slots": slots,
            "status": "active",
            "created_at": _utc_iso(),
        }
        self._write_snapshot_yaml(payload)
        logger.info("配置快照 v%d 已捕获（项目 %s，触发提案 %s）", version, project_id, trigger_proposal)
        return version

    def _switch_store(self, project_id: str, version: int) -> bool:
        docs = self._iter_snapshot_docs(project_id)
        versions = {int(d.get("version") or 0) for d in docs}
        if version not in versions:
            return False
        resolved = self._resolve_store(project_id, version)
        self._restore_slots_store(project_id, resolved)
        for data in docs:
            ver = int(data.get("version") or 0)
            if data.get("status") == "active":
                data["status"] = "superseded"
                self._write_snapshot_yaml(data)
            if ver == version:
                data["status"] = "rolled_back_to"
                self._write_snapshot_yaml(data)
        logger.info("已切换到快照 v%d（项目 %s）", version, project_id)
        return True
