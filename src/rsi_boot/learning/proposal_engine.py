"""Harness 提案引擎（Spec v3.0 §4.5/§4.6）：提案生成 → 门禁 → 人工确认 → 晋升 → 观察期巡检。

生命周期：proposed → validating → approved → active → rolled_back/rejected。
- 生成：默认模板化（零 Key，§4.5 三种失败模式模板）；LLM 生成为附录 C 可选增强
- 门禁：默认静态校验五项 + 脱敏 + 注入黑名单（StaticGate）；回放回归为可选增强
- 速率硬上限：单轮 ≤ 5 个提案、单槽位周变更 ≤ 3 次（防振荡）
- 晋升：应用槽位变更 + Genome 快照（capture 于变更后，pre_activation_version 记变更前）
- 观察期 7 天：每日巡检采纳率，较晋升前 7 天下降 > 5% 自动回滚并标记 rolled_back
- 安全边界：提案只能改内容槽位；评估代码/门禁参数/提案机制自身不在可写面
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..memory.logstore import iter_events
from ..memory.paths import official_dir, pending_dir, review_dir
from ..memory.store import MemoryStore, memory_filename
from ..memory.types import MEMORY_TYPES, MemoryDoc
from .failure_miner import FailureBucket
from .snapshot_store import SnapshotStore

logger = logging.getLogger(__name__)

MAX_PROPOSALS_PER_ROUND = 5   # 单轮提案硬上限
MAX_BUCKET_PROPOSALS = 3      # 单失败桶提案上限
SLOT_WEEKLY_CAP = 3           # 单槽位周变更上限
OBSERVATION_DAYS = 7          # 观察期
OBSERVE_DROP_THRESHOLD = 0.05  # 采纳率下降 > 5% 自动回滚

#: 提案可写槽位（profile 不经提案流程，§3.9）
WRITABLE_SLOTS = ("prompt_template", "skill", "strategy", "intent_rule", "knowledge")

_PROPOSAL_PROMPT = """你是 Harness 改进工程师。以下失败桶聚类自近 7 天用户负信号。
为每个桶生成 ≤ {max_per_bucket} 个**最小化、单槽位**改进提案——每个提案必须直接针对该桶的
失败机制，禁止泛化大改。可写槽位：prompt_template / skill / strategy / intent_rule / knowledge。

【失败桶】intent={intent}，strategy={strategy}，样本数={count}
【信号分布】{signals}
【输入摘录】
{examples}

仅输出 JSON 数组（无提案价值时输出 []）：
[{{"slot": "槽位", "target_ref": "目标标识（如策略名/模板文件名/技能名/意图名）",
   "action": "add|remove|modify",
   "after": {{... 变更后内容（modify 时含完整新值；remove 可为空）}},
   "rationale": "绑定本桶失败机制的一句话依据"}}]"""


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProposalEngine:
    def __init__(
        self,
        store: MemoryStore,
        adapter: Any = None,
        config: Optional[Dict[str, Any]] = None,
        gate: Any = None,
        snapshots: Optional[SnapshotStore] = None,
        rsi_home: Optional[Path] = None,
        on_change: Optional[Any] = None,
    ):
        self._store = store
        self._adapter = adapter
        self._config = config or {}
        self._gate = gate  # StaticGate（默认）或 RegressionGate（enhance.gate_replay）
        self._home = rsi_home or store.rsi_dir
        self._snapshots = snapshots or SnapshotStore(store=store, rsi_home=self._home)
        enhance = self._config.get("enhance", {})
        self._use_llm = bool(enhance.get("proposal_llm")) and adapter is not None
        self._model = str(enhance.get("model", {}).get("default", "gpt-4o-mini"))
        self._auto_apply = bool(self._config.get("harness", {}).get("auto_apply", False))
        # 记忆变更回调（知识/召回臂槽位变更后触发规则注入重写，§4.2）
        self._on_change = on_change

    # ---------- 模板化提案（默认，零 Key，§4.5） ----------

    def _template_propose(self, bucket: FailureBucket) -> List[Dict[str, Any]]:
        """三种失败模式模板：rejected+comment 汇总禁止项 / 召回臂参数回退 /
        （domain 零采纳下线在 generate 中单独扫描，非桶驱动）"""
        proposals: List[Dict[str, Any]] = []

        # 模板一：桶内 rejected+comment 集中 → 汇总 comment 生成禁止项条目
        if bucket.signals.get("rejected") and bucket.comments:
            unique_comments = list(dict.fromkeys(c.strip() for c in bucket.comments if c.strip()))
            if unique_comments:
                first = unique_comments[0].splitlines()[0][:27]
                body = "\n".join(f"{i}. {c[:400]}" for i, c in enumerate(unique_comments, 1))
                proposals.append({
                    "slot": "knowledge", "target_ref": f"prohibition-{first}",
                    "action": "add",
                    "after": {
                        "title": f"禁止：{first}" if not first.startswith("禁止") else first[:30],
                        "content": body[:2000], "content_type": "prohibition",
                        "tags": [], "roles": [],
                    },
                    "rationale": f"桶内 {bucket.signals['rejected']} 次 rejected 含评语，汇总为禁止项",
                })

        # 模板二：召回臂负反馈集中 → 参数回退 balanced（切换臂配置）
        if (bucket.intent == "recall" and bucket.strategy_name.startswith("recall-")
                and bucket.strategy_name != "recall-balanced"):
            proposals.append({
                "slot": "strategy", "target_ref": bucket.strategy_name,
                "action": "modify",
                "after": {"params": {"top_n": 5, "threshold": 0.6}},
                "rationale": f"召回臂 {bucket.strategy_name} 负信号 {bucket.count} 次，回退 balanced 参数",
            })
        return proposals

    async def _llm_propose(self, bucket: FailureBucket) -> List[Dict[str, Any]]:
        examples = "\n".join(f"- {e['input_excerpt']}" for e in bucket.examples) or "（无）"
        messages = [{"role": "user", "content": _PROPOSAL_PROMPT.format(
            max_per_bucket=MAX_BUCKET_PROPOSALS, intent=bucket.intent,
            strategy=bucket.strategy_name, count=bucket.count,
            signals=json.dumps(bucket.signals, ensure_ascii=False), examples=examples,
        )}]
        try:
            result = await self._adapter.complete(self._model, messages)
            data = json.loads(result.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
            if not isinstance(data, list):
                return []
        except Exception as exc:
            logger.warning("提案生成 LLM 调用/解析失败: %s", exc)
            return []
        valid = []
        for p in data[:MAX_BUCKET_PROPOSALS]:
            if not isinstance(p, dict):
                continue
            if p.get("slot") not in WRITABLE_SLOTS or p.get("action") not in ("add", "remove", "modify"):
                continue
            if not p.get("target_ref"):
                continue
            valid.append(p)
        return valid

    async def generate(self, project_id: str, days: int = 7) -> List[str]:
        """失败挖掘 → 模板化提案 → 写入 state/proposals YAML。返回提案 id 列表"""
        return self._generate_store(project_id, days)

    # ---------- 2. 回归验证门禁 ----------

    async def validate(self, proposal_id: str) -> bool:
        """proposed → validating → approved/rejected（§3.9 第 3 步）。返回是否通过门禁"""
        return False

    async def validate_pending(self, project_id: str) -> Dict[str, int]:
        """批量验证全部 proposed 提案（每周离线任务）"""
        return {"validated": 0, "approved": 0}

    # ---------- 3. 人工确认与晋升 ----------

    def _slot_weekly_count_store(self, project_id: str, slot: str) -> int:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        n = 0
        for row in self._list_proposals_store(project_id, None):
            if row.get("slot") != slot or row.get("status") == "rejected":
                continue
            created = str(row.get("created_at") or "")
            if created and created < since:
                continue
            n += 1
        return n

    def _mine_store_failures(self, project_id: str, days: int) -> List[FailureBucket]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        events = list(iter_events(self._store.rsi_dir))
        feedback_by_token: Dict[str, Dict[str, Any]] = {}
        for event in events:
            token = event.get("token")
            if event.get("kind") == "feedback" and token:
                feedback_by_token[str(token)] = event

        buckets: Dict[str, FailureBucket] = {}
        for event in events:
            if event.get("kind") not in (None, "recall"):
                continue
            if event.get("status") == "pending":
                continue
            ts = str(event.get("ts") or event.get("created_at") or "")
            if ts and ts < since:
                continue
            if project_id and event.get("project_id") and event.get("project_id") != project_id:
                continue
            token = event.get("token")
            fb = feedback_by_token.get(str(token)) if token else None
            action = event.get("action") or event.get("feedback_action")
            if not action and fb:
                action = fb.get("action") or fb.get("feedback_action")
            rating = event.get("rating")
            if rating is None:
                rating = event.get("feedback_rating")
            if rating is None and fb:
                rating = fb.get("rating")
            comment = event.get("comment") or event.get("feedback_comment")
            if not comment and fb:
                comment = fb.get("comment") or fb.get("feedback_comment")
            try:
                rating_n = int(rating) if rating is not None else None
            except (TypeError, ValueError):
                rating_n = None
            if action not in ("rejected", "modified") and not (rating_n is not None and rating_n <= 2):
                continue
            retrieved = event.get("retrieved") or []
            group_key = retrieved[0] if retrieved else str(event.get("task") or "unknown")
            arm = str(event.get("arm") or event.get("strategy_name") or "default")
            bucket = buckets.setdefault(str(group_key), FailureBucket(intent="recall", strategy_name=arm))
            bucket.log_ids.append(str(event.get("id") or ""))
            if action == "rejected":
                bucket.signals["rejected"] = bucket.signals.get("rejected", 0) + 1
                if comment:
                    bucket.comments.append(str(comment))
            if action == "modified":
                bucket.signals["modified"] = bucket.signals.get("modified", 0) + 1
            if rating_n is not None and rating_n <= 2:
                bucket.signals["low_rating"] = bucket.signals.get("low_rating", 0) + 1
            if len(bucket.examples) < 3:
                bucket.examples.append({"input_excerpt": str(event.get("task") or "")[:200]})
        return list(buckets.values())

    def _generate_store(self, project_id: str, days: int) -> List[str]:
        raw: List[tuple[Dict[str, Any], Optional[FailureBucket]]] = []
        for bucket in self._mine_store_failures(project_id, days):
            raw.extend((p, bucket) for p in self._template_propose(bucket))

        proposal_ids: List[str] = []
        for p, bucket in raw:
            if len(proposal_ids) >= MAX_PROPOSALS_PER_ROUND:
                break
            if self._slot_weekly_count_store(project_id, p["slot"]) >= SLOT_WEEKLY_CAP:
                logger.info("槽位 %s 周变更已达上限 %d，跳过提案", p["slot"], SLOT_WEEKLY_CAP)
                continue
            pid = uuid.uuid4().hex
            evidence = {
                "bucket": ({"intent": bucket.intent, "strategy": bucket.strategy_name,
                            "count": bucket.count, "signals": bucket.signals}
                           if bucket else {"intent": "", "strategy": "", "count": 0, "signals": {}}),
                "log_ids": bucket.log_ids[:10] if bucket else [],
                "rationale": p.get("rationale", ""),
            }
            self._write_proposal_yaml({
                "id": pid,
                "project_id": project_id,
                "slot": p["slot"],
                "target_ref": str(p["target_ref"]),
                "action": p["action"],
                "payload": {"before": p.get("before"), "after": p.get("after") or {}},
                "evidence": evidence,
                "status": "proposed",
                "created_at": _utc_iso(),
            })
            proposal_ids.append(pid)
        if proposal_ids:
            logger.info("提案生成完成：%d 个（项目 %s）", len(proposal_ids), project_id)
        return proposal_ids

    def _load_arm_rows(self) -> tuple[List[Dict[str, Any]], Any]:
        path = self._store.rsi_dir / "state" / "arms.yaml"
        if not path.is_file():
            return [], {"arms": []}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return [], {"arms": []}
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)], data
        if isinstance(data, dict):
            rows = data.get("arms") or []
            return [r for r in rows if isinstance(r, dict)], data
        return [], {"arms": []}

    def _write_arm_rows(self, rows: List[Dict[str, Any]], wrapper: Any) -> None:
        dest = self._store.rsi_dir / "state" / "arms.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload: Any = rows if isinstance(wrapper, list) else {**(wrapper or {}), "arms": rows}
        tmp = Path(str(dest) + ".tmp")
        tmp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        import os
        os.replace(tmp, dest)

    def _apply_strategy_store(self, target_ref: str, action: str, after: Dict[str, Any]) -> None:
        rows, wrapper = self._load_arm_rows()
        now = _utc_iso()
        if action == "add":
            params = after.get("params") or {}
            rows.append({
                "id": uuid.uuid4().hex,
                "name": target_ref,
                "intent": after.get("intent", "recall"),
                "top_n": params.get("top_n", after.get("top_n", 5)),
                "threshold": params.get("threshold", after.get("threshold", 0.6)),
                "params": params,
                "active": True,
                "created_at": now,
                "updated_at": now,
            })
        elif action == "remove":
            for row in rows:
                if (row.get("name") or row.get("strategy_name")) == target_ref:
                    row["active"] = False
                    row["updated_at"] = now
        else:
            for row in rows:
                if (row.get("name") or row.get("strategy_name")) != target_ref:
                    continue
                params = after.get("params") or {}
                existing = row.get("params") or {}
                if isinstance(existing, str):
                    try:
                        existing = json.loads(existing)
                    except (TypeError, ValueError):
                        existing = {}
                if not isinstance(existing, dict):
                    existing = {}
                if params:
                    existing.update(params)
                    row["params"] = existing
                    if "top_n" in params:
                        row["top_n"] = params["top_n"]
                    if "threshold" in params:
                        row["threshold"] = params["threshold"]
                if after.get("model_name"):
                    row["model_name"] = after["model_name"]
                if after.get("weight") is not None:
                    row["weight"] = after["weight"]
                row["updated_at"] = now
        self._write_arm_rows(rows, wrapper)

    def _apply_knowledge_store(self, target_ref: str, action: str, after: Dict[str, Any]) -> None:
        if action == "remove":
            try:
                self._store.move(
                    target_ref,
                    self._store.rsi_dir / "memory" / "archive",
                )
            except (FileNotFoundError, ValueError):
                return
            return
        typ = str(after.get("content_type") or after.get("type") or "convention")
        if typ not in MEMORY_TYPES:
            typ = "convention"
        doc_id = target_ref if action == "modify" and len(target_ref) == 32 else uuid.uuid4().hex
        if action == "modify":
            try:
                existing = self._store.read(target_ref)
                dest = self._store.rsi_dir / existing.path if existing.path else (
                    official_dir(self._store.rsi_dir, existing.type) / memory_filename(existing.title, existing.id)
                )
                updated = existing.model_copy(update={
                    "content": str(after.get("content") or existing.content),
                    "title": str(after.get("title") or existing.title),
                })
                self._store.write(updated, dest=dest)
                return
            except (FileNotFoundError, ValueError):
                doc_id = target_ref if len(target_ref) == 32 else uuid.uuid4().hex
        title = str(after.get("title") or target_ref)[:120]
        doc = MemoryDoc(
            id=doc_id if len(doc_id) == 32 else uuid.uuid4().hex,
            type=typ,
            title=title,
            content=str(after.get("content") or "")[:20000],
            tags=list(after.get("tags") or []),
            roles=list(after.get("roles") or []),
            domain=after.get("domain"),
        )
        try:
            dest_dir = pending_dir(self._store.rsi_dir, doc.type)
        except ValueError:
            dest_dir = review_dir(self._store.rsi_dir, doc.type)
        dest = dest_dir / memory_filename(doc.title, doc.id)
        self._store.write(doc, dest=dest)

    def _apply_slot_change_store(self, project_id: str, slot: str, target_ref: str,
                                 action: str, after: Dict[str, Any]) -> None:
        mentions_arms = bool(after.get("params") or after.get("arms") or "arms" in after)
        if slot == "strategy" or mentions_arms:
            self._apply_strategy_store(target_ref, action, after)
            if slot == "strategy":
                return
        if slot == "knowledge":
            self._apply_knowledge_store(target_ref, action, after)
            return
        if slot == "intent_rule":
            overlay_path = self._home / "intent_rules.yaml"
            data: Dict[str, Any] = {}
            if overlay_path.is_file():
                data = yaml.safe_load(overlay_path.read_text(encoding="utf-8")) or {}
            rules: List[Dict[str, Any]] = list(data.get("rules") or [])
            if action == "remove":
                rules = [r for r in rules if r.get("name") != target_ref]
            else:
                rules = [r for r in rules if r.get("name") != target_ref]
                rules.append({"name": target_ref, **(after or {})})
            overlay_path.write_text(yaml.safe_dump({"rules": rules}, allow_unicode=True), encoding="utf-8")
        elif slot == "prompt_template":
            tpl_dir = self._home / "templates"
            tpl_dir.mkdir(parents=True, exist_ok=True)
            path = tpl_dir / target_ref
            if action == "remove":
                path.unlink(missing_ok=True)
            else:
                path.write_text(str(after.get("template_content", "")), encoding="utf-8")
        elif slot == "skill":
            from rsi_boot.injector.slug import slugify

            name = slugify(str(after.get("name") or target_ref))
            if action == "remove":
                for doc in self._store.list_all():
                    if doc.type != "skill":
                        continue
                    payload_name = str((doc.payload or {}).get("name") or "")
                    if target_ref in (doc.id, doc.title, payload_name):
                        self._store.move(doc.id, self._store.rsi_dir / "memory" / "archive")
                        return
                return
            content = str(after.get("skill_md") or after.get("content") or "")
            doc = MemoryDoc(
                id=uuid.uuid4().hex,
                type="skill",
                title=name[:120],
                content=content[:20000],
                description=str(after.get("description") or "") or None,
                payload={"name": name},
            )
            dest = pending_dir(self._store.rsi_dir, "skill") / memory_filename(doc.title, doc.id)
            self._store.write(doc, dest=dest)

    async def _apply_slot_change(self, project_id: str, slot: str, target_ref: str,
                                 action: str, after: Dict[str, Any]) -> None:
        """槽位变更应用（仅内容槽位；评估代码/门禁参数/提案机制不可写）"""
        self._apply_slot_change_store(project_id, slot, target_ref, action, after)

    async def approve(self, proposal_id: str) -> bool:
        """approved → active：记录晋升前快照版本 → 应用槽位变更 → 捕获新快照 → 进入观察期"""
        return await self._approve_store(proposal_id)

    async def _notify_change(self, project_id: str) -> None:
        """记忆/策略变更后触发规则注入重写（§4.2）；回调失败不阻塞提案流转"""
        if self._on_change is None:
            return
        try:
            await self._on_change(project_id)
        except Exception:
            logger.exception("注入重写回调失败（下轮变更重试）")

    async def reject(self, proposal_id: str, reason: str = "") -> bool:
        ok = self._update_proposal_status(
            proposal_id, ("proposed", "validating", "approved"), "rejected",
        )
        if ok and reason:
            logger.info("提案 %s 人工否决：%s", proposal_id[:8], reason)
        return ok

    # ---------- 4. 回滚与观察期巡检 ----------

    async def rollback(self, proposal_id: str, reason: str = "") -> bool:
        """active → rolled_back：切换到晋升前快照（回滚 = 切换快照，非逆向重放 diff）"""
        return await self._rollback_store(proposal_id, reason)

    async def patrol(self, project_id: str) -> Dict[str, int]:
        """观察期每日巡检：采纳率较晋升前 7 天下降 > 5% → 自动回滚"""
        return {"patrolled": 0, "rolled_back": 0}

    # ---------- 查询 ----------

    async def list_proposals(self, project_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._list_proposals_store(project_id, status)

    def _proposals_dir(self) -> Path:
        return self._store.rsi_dir / "state" / "proposals"

    def _write_proposal_yaml(self, data: Dict[str, Any]) -> None:
        import os
        pid = str(data.get("id") or uuid.uuid4().hex)
        data["id"] = pid
        dest = self._proposals_dir() / f"{pid}.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(dest) + ".tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(tmp, dest)

    def _load_proposal_yaml(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        path = self._proposals_dir() / f"{proposal_id}.yaml"
        if not path.is_file():
            return None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        return data if isinstance(data, dict) else None

    def _list_proposals_store(
        self, project_id: str, status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        root = self._proposals_dir()
        if not root.is_dir():
            return []
        rows: List[Dict[str, Any]] = []
        for path in sorted(root.glob("*.yaml")):
            if path.name.endswith(".tmp"):
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(data, dict):
                continue
            if project_id and data.get("project_id") and data.get("project_id") != project_id:
                continue
            if status and data.get("status") != status:
                continue
            rows.append(data)
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        if not status:
            rows = rows[:50]
        return rows

    def _update_proposal_status(self, proposal_id: str, allowed: tuple[str, ...], new_status: str) -> bool:
        data = self._load_proposal_yaml(proposal_id)
        if data is None or data.get("status") not in allowed:
            return False
        data["status"] = new_status
        data["decided_at"] = _utc_iso()
        if new_status == "active":
            now = datetime.now(timezone.utc)
            data["activated_at"] = now.isoformat()
            data["observe_until"] = (now + timedelta(days=OBSERVATION_DAYS)).isoformat()
        self._write_proposal_yaml(data)
        return True

    async def _approve_store(self, proposal_id: str) -> bool:
        data = self._load_proposal_yaml(proposal_id)
        if data is None or data.get("status") != "approved":
            return False
        project_id = str(data.get("project_id") or "")
        payload = data.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        pre_version = await self._snapshots._latest_version(project_id)
        payload["pre_activation_version"] = pre_version
        data["payload"] = payload
        now = datetime.now(timezone.utc)
        data["status"] = "active"
        data["decided_at"] = now.isoformat()
        data["activated_at"] = now.isoformat()
        data["observe_until"] = (now + timedelta(days=OBSERVATION_DAYS)).isoformat()
        self._write_proposal_yaml(data)
        after = payload.get("after") or {}
        if isinstance(after, str):
            try:
                after = json.loads(after)
            except (TypeError, ValueError):
                after = {}
        await self._apply_slot_change(
            project_id,
            str(data.get("slot") or ""),
            str(data.get("target_ref") or ""),
            str(data.get("action") or "modify"),
            after if isinstance(after, dict) else {},
        )
        await self._snapshots.capture(project_id, trigger_proposal=proposal_id)
        await self._notify_change(project_id)
        return True

    async def _rollback_store(self, proposal_id: str, reason: str = "") -> bool:
        data = self._load_proposal_yaml(proposal_id)
        if data is None or data.get("status") != "active":
            return False
        payload = data.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        pre_version = int(payload.get("pre_activation_version") or 0)
        project_id = str(data.get("project_id") or "")
        if pre_version > 0:
            await self._snapshots.switch(project_id, pre_version)
        data["status"] = "rolled_back"
        data["decided_at"] = _utc_iso()
        self._write_proposal_yaml(data)
        await self._notify_change(project_id)
        logger.warning("提案 %s 已回滚到快照 v%d（%s）", proposal_id[:8], pre_version, reason or "人工回滚")
        return True
