"""One-shot sqlite → file memory. Runtime still uses sqlite until Task 11b."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from rsi_boot.cli.progress import Progress
from rsi_boot.data.sqlite import SQLiteClient
from rsi_boot.injector.rule_injector import RuleInjector
from rsi_boot.memory.logstore import append_event, iter_events
from rsi_boot.memory.paths import official_dir, pending_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc, type_from_legacy
from rsi_boot.project import project_rsi_dir
from rsi_boot.rag.index import build_index
from rsi_boot.ux.lang import locale_lang
from rsi_boot.ux.messages import t

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_TABLES = (
    "knowledge_items",
    "interaction_logs",
    "strategy_configs",
    "rule_conflicts",
    "harness_proposals",
    "config_snapshots",
    "user_profiles",
    "rule_artifacts",
    "extraction_candidates",
    "project_configs",
)
_IDENTITY_KEYS = frozenset({"project_id", "root"})


class MemoryMigrateHalfError(Exception):
    """Write failed; rsi.db must stay in place."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_dict(row: Any) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _parse_list(raw: Any) -> list:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return val if isinstance(val, list) else []


def _parse_obj(raw: Any) -> dict:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return val if isinstance(val, dict) else {}


def _doc_id(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if _HEX32.fullmatch(text):
        return text
    return uuid.uuid4().hex


def _dest_for(rsi_dir: Path, typ: str, status: str, title: str, doc_id: str) -> Path:
    name = memory_filename(title, doc_id)
    if status == "pending_review":
        try:
            return pending_dir(rsi_dir, typ) / name
        except ValueError:
            try:
                folder = official_dir(rsi_dir, typ).name
            except ValueError:
                return rsi_dir / "memory" / "pending" / name
            return rsi_dir / "memory" / "pending" / folder / name
    if status == "active":
        try:
            return official_dir(rsi_dir, typ) / name
        except ValueError:
            return rsi_dir / "memory" / "archive" / name
    return rsi_dir / "memory" / "archive" / name


async def _table_exists(conn: Any, name: str) -> bool:
    async with conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ) as cur:
        return await cur.fetchone() is not None


async def _fetch_all(conn: Any, table: str) -> list[dict[str, Any]]:
    if not await _table_exists(conn, table):
        return []
    async with conn.execute(f"SELECT * FROM {table}") as cur:
        rows = await cur.fetchall()
    return [_row_dict(r) for r in rows]


def _knowledge_pairs(
    rsi_dir: Path, rows: list[dict[str, Any]]
) -> tuple[list[tuple[MemoryDoc, Path]], set[str]]:
    pairs: list[tuple[MemoryDoc, Path]] = []
    ids: set[str] = set()
    for row in rows:
        typ, extra_update = type_from_legacy(str(row.get("content_type") or "documentation"))
        extra = dict(extra_update)
        doc_id = _doc_id(row.get("id"))
        title = str(row.get("title") or "item")[:120]
        content = str(row.get("content") or "")[:20000]
        if typ not in (
            "prohibition",
            "convention",
            "documentation",
            "skill",
            "episode",
            "gene_case",
            "teaching_case",
            "pattern",
        ):
            typ = "documentation"
            extra.setdefault("legacy_type", str(row.get("content_type") or ""))
        doc = MemoryDoc(
            id=doc_id,
            type=typ,
            title=title,
            content=content,
            domain=row.get("domain") or None,
            tags=[str(x) for x in _parse_list(row.get("tags"))],
            roles=[str(x) for x in _parse_list(row.get("roles"))],
            source=row.get("source_url") or None,
            created_at=row.get("created_at") or None,
            updated_at=row.get("updated_at") or None,
            extra=extra,
        )
        status = str(row.get("status") or "active")
        dest = _dest_for(rsi_dir, typ, status, title, doc_id)
        pairs.append((doc, dest))
        ids.add(doc_id)
    return pairs, ids


def _map_events(
    rows: list[dict[str, Any]], knowledge_ids: set[str]
) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    unmapped = 0
    for row in rows:
        retrieved: list[str] = []
        legacy: list[str] = []
        for tag in _parse_list(row.get("retrieved_tags")):
            text = str(tag)
            hid = text.strip().lower()
            if _HEX32.fullmatch(hid) and hid in knowledge_ids:
                retrieved.append(hid)
            else:
                legacy.append(text)
                unmapped += 1
        intent = row.get("intent") or ""
        event: dict[str, Any] = {
            "id": row.get("id"),
            "ts": row.get("created_at") or _utc_now(),
            "kind": "recall" if intent == "recall" else (intent or "recall"),
            "task": row.get("raw_input") or "",
            "retrieved": retrieved,
            "token": row.get("feedback_token"),
            "arm": row.get("strategy_name"),
            "latency_ms": row.get("latency_ms"),
        }
        if legacy:
            event["retrieved_legacy_tags"] = legacy
        if row.get("response_excerpt"):
            event["excerpt"] = row["response_excerpt"]
        if row.get("feedback_action"):
            event["action"] = row["feedback_action"]
        if row.get("feedback_rating") is not None:
            event["rating"] = row["feedback_rating"]
        if row.get("feedback_comment"):
            event["comment"] = row["feedback_comment"]
        events.append(event)
    return events, unmapped


def _arms_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    arms: list[dict[str, Any]] = []
    for row in rows:
        params = _parse_obj(row.get("params"))
        arms.append(
            {
                "id": row.get("id"),
                "name": row.get("strategy_name"),
                "intent": row.get("intent"),
                "role": row.get("role"),
                "top_n": params.get("top_n"),
                "threshold": params.get("threshold"),
                "alpha": row.get("alpha"),
                "beta": row.get("beta"),
                "exposure": row.get("exposure_count"),
                "active": bool(row.get("is_active")),
                "model_name": row.get("model_name"),
                "params": params or None,
                "project_id": row.get("project_id"),
            }
        )
    return arms


def _conflicts_payload(
    rows: list[dict[str, Any]], queue: Any
) -> dict[str, Any]:
    conflicts = []
    for row in rows:
        conflicts.append(
            {
                "id": row.get("id"),
                "item_id": row.get("item_id"),
                "user_rule_path": row.get("user_rule_path"),
                "excerpt": row.get("user_rule_excerpt"),
                "type": row.get("conflict_type"),
                "status": row.get("status"),
                "resolution_note": row.get("resolution_note"),
                "detected_at": row.get("detected_at"),
                "resolved_at": row.get("resolved_at"),
            }
        )
    payload: dict[str, Any] = {"conflicts": conflicts}
    if queue is not None:
        payload["queue"] = queue
    return payload


def _write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _merge_json(path: Path, incoming: dict[str, Any]) -> None:
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            existing = loaded
    for key, value in incoming.items():
        if key not in existing:
            existing[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _append_error(rsi_dir: Path, exc: BaseException, lang: str) -> None:
    path = rsi_dir / "logs" / "errors.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": _utc_now(),
        "kind": "memory_migrate",
        "message": t("MIGRATE_HALF", lang),
        "error": str(exc),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _find_skill_mds(project_root: Path, rsi_dir: Path) -> list[Path]:
    found: list[Path] = []
    roots = [
        rsi_dir / "skills",
        project_root / "config" / "skills",
        rsi_dir / "config" / "skills",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        found.extend(sorted(root.glob("*/SKILL.md")))
    return found


def _skill_docs(project_root: Path, rsi_dir: Path) -> list[tuple[MemoryDoc, Path]]:
    try:
        from rsi_boot.learning.skill_loader import parse_skill_md
    except ImportError:
        parse_skill_md = None  # type: ignore[assignment]
    pairs: list[tuple[MemoryDoc, Path]] = []
    for path in _find_skill_mds(project_root, rsi_dir):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        name = path.parent.name
        description = ""
        body = text
        tags: list[str] = []
        payload: dict[str, Any] = {"name": name}
        if parse_skill_md is not None:
            parsed = parse_skill_md(text, str(path))
            if parsed is not None:
                name = parsed.name or name
                description = parsed.description or ""
                body = parsed.body or text
                tags = list(parsed.keywords)
                payload = {
                    "name": parsed.name,
                    "intents": list(parsed.intents),
                }
        doc_id = uuid.uuid4().hex
        title = name[:120]
        doc = MemoryDoc(
            id=doc_id,
            type="skill",
            title=title,
            content=body[:20000],
            description=description or None,
            tags=tags,
            payload=payload,
            extra={"legacy_type": "skill_md"},
        )
        dest = official_dir(rsi_dir, "skill") / name / "skill.yaml"
        pairs.append((doc, dest))
    return pairs


def _rename_legacy_db(rsi_dir: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    bak = f"rsi.db.bak-{stamp}"
    mapping = (
        ("rsi.db", bak),
        ("rsi.db-wal", f"{bak}-wal"),
        ("rsi.db-shm", f"{bak}-shm"),
    )
    last_err: OSError | None = None
    for src_name, dest_name in mapping:
        src = rsi_dir / src_name
        if not src.exists():
            continue
        dest = rsi_dir / dest_name
        for attempt in range(8):
            try:
                os.replace(src, dest)
                last_err = None
                break
            except OSError as exc:
                last_err = exc
                time.sleep(0.05 * (attempt + 1))
        if last_err is not None and src.exists():
            raise last_err
    return bak


def _write_index(rsi_dir: Path, docs: list[MemoryDoc]) -> None:
    index = build_index(docs)
    cache = rsi_dir / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "inverted.json").write_text(
        json.dumps(index, ensure_ascii=False),
        encoding="utf-8",
    )


async def migrate_workspace(
    project_root: Path,
    *,
    dry_run: bool = False,
    progress: Progress | None = None,
    lang: str | None = None,
) -> dict:
    """Read-only sqlite. Return counts. Only non-dry_run writes files and renames db."""
    loc = lang or locale_lang()
    root = Path(project_root)
    rsi_dir = project_rsi_dir(root)
    db_path = rsi_dir / "rsi.db"
    if not db_path.is_file():
        raise FileNotFoundError(t("MIGRATE_NO_DB", loc))

    if progress is not None:
        progress.start(8, title=t("MIGRATE_TITLE", loc, root=str(root)))

    table_counts = {name: 0 for name in _TABLES}
    knowledge_pairs: list[tuple[MemoryDoc, Path]] = []
    knowledge_ids: set[str] = set()
    events: list[dict[str, Any]] = []
    unmapped = 0
    arms: list[dict[str, Any]] = []
    conflicts: dict[str, Any] = {"conflicts": []}
    proposals: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    project_rows: list[dict[str, Any]] = []
    skill_pairs: list[tuple[MemoryDoc, Path]] = []

    client = SQLiteClient(db_path)
    try:
        if progress is not None:
            progress.phase(t("MIGRATE_PHASE_OPEN", loc))
        conn = await client.connect()

        if progress is not None:
            progress.phase(t("MIGRATE_PHASE_KNOWLEDGE", loc))
        knowledge_rows = await _fetch_all(conn, "knowledge_items")
        table_counts["knowledge_items"] = len(knowledge_rows)
        knowledge_pairs, knowledge_ids = _knowledge_pairs(rsi_dir, knowledge_rows)

        if progress is not None:
            progress.phase(t("MIGRATE_PHASE_LOGS", loc))
        log_rows = await _fetch_all(conn, "interaction_logs")
        table_counts["interaction_logs"] = len(log_rows)
        events, unmapped = _map_events(log_rows, knowledge_ids)

        if progress is not None:
            progress.phase(t("MIGRATE_PHASE_STATE", loc))
        strategy_rows = await _fetch_all(conn, "strategy_configs")
        table_counts["strategy_configs"] = len(strategy_rows)
        arms = _arms_payload(strategy_rows)

        conflict_rows = await _fetch_all(conn, "rule_conflicts")
        table_counts["rule_conflicts"] = len(conflict_rows)
        queue = None
        queue_path = rsi_dir / "host_judge_queue.json"
        if queue_path.is_file():
            try:
                queue = json.loads(queue_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                queue = None
        conflicts = _conflicts_payload(conflict_rows, queue)

        proposals = await _fetch_all(conn, "harness_proposals")
        table_counts["harness_proposals"] = len(proposals)
        snapshots = await _fetch_all(conn, "config_snapshots")
        table_counts["config_snapshots"] = len(snapshots)
        profile_rows = await _fetch_all(conn, "user_profiles")
        table_counts["user_profiles"] = len(profile_rows)
        for row in profile_rows:
            profiles.append(
                {
                    "user_id": row.get("user_id"),
                    "project_id": row.get("project_id"),
                    "profile": _parse_obj(row.get("profile_data")),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
        artifacts = await _fetch_all(conn, "rule_artifacts")
        table_counts["rule_artifacts"] = len(artifacts)
        candidates = await _fetch_all(conn, "extraction_candidates")
        table_counts["extraction_candidates"] = len(candidates)
        project_rows = await _fetch_all(conn, "project_configs")
        table_counts["project_configs"] = len(project_rows)
        skill_pairs = _skill_docs(root, rsi_dir)
    finally:
        await client.close()

    bak = ""
    written_docs = [doc for doc, _ in knowledge_pairs] + [doc for doc, _ in skill_pairs]

    if progress is not None:
        progress.phase(t("MIGRATE_PHASE_WRITE", loc), total=len(knowledge_pairs) + len(events))

    if not dry_run:
        try:
            store = MemoryStore(rsi_dir)
            for doc, dest in knowledge_pairs:
                store.write(doc, dest=dest)
                if progress is not None:
                    progress.tick()
            for doc, dest in skill_pairs:
                store.write(doc, dest=dest)
            existing_ids = {
                str(ev.get("id")) for ev in iter_events(rsi_dir) if ev.get("id")
            }
            for event in events:
                eid = event.get("id")
                if eid is not None and str(eid) in existing_ids:
                    if progress is not None:
                        progress.tick()
                    continue
                append_event(rsi_dir, event)
                if eid is not None:
                    existing_ids.add(str(eid))
                if progress is not None:
                    progress.tick()
            _write_yaml(rsi_dir / "state" / "arms.yaml", arms)
            _write_yaml(rsi_dir / "state" / "conflicts.yaml", conflicts)
            for row in proposals:
                pid = str(row.get("id") or uuid.uuid4().hex)
                payload = dict(row)
                if "payload" in payload:
                    parsed = _parse_obj(payload["payload"])
                    if parsed:
                        payload["payload"] = parsed
                _write_yaml(rsi_dir / "state" / "proposals" / f"{pid}.yaml", payload)
            for row in snapshots:
                ver = row.get("version")
                sid = str(row.get("id") or ver or uuid.uuid4().hex)
                dest_dir = rsi_dir / "state" / "snapshots" / str(ver if ver is not None else sid)
                payload = dict(row)
                if "slots" in payload:
                    parsed = _parse_obj(payload["slots"])
                    if parsed:
                        payload["slots"] = parsed
                _write_yaml(dest_dir / "snapshot.yaml", payload)
            _write_yaml(rsi_dir / "state" / "profile.yaml", profiles)
            _write_yaml(rsi_dir / "state" / "artifacts.yaml", artifacts)
            cand_path = rsi_dir / "logs" / "candidates.jsonl"
            if candidates:
                cand_path.parent.mkdir(parents=True, exist_ok=True)
                with cand_path.open("a", encoding="utf-8") as fh:
                    for row in candidates:
                        record = {
                            "id": row.get("id"),
                            "ts": row.get("created_at") or _utc_now(),
                            "type": row.get("candidate_type"),
                            "question": row.get("question"),
                            "answer": row.get("answer"),
                            "source_event_id": row.get("source_log_id"),
                            "status": row.get("status"),
                        }
                        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            ident_in: dict[str, Any] = {}
            manifest_in: dict[str, Any] = {}
            for row in project_rows:
                data = _parse_obj(row.get("config_data"))
                if not data:
                    data = {
                        "project_id": row.get("project_id"),
                        "updated_at": row.get("updated_at"),
                    }
                for key, value in data.items():
                    if key in _IDENTITY_KEYS:
                        ident_in[key] = value
                    else:
                        manifest_in[key] = value
                if row.get("project_id") and "project_id" not in ident_in:
                    ident_in["project_id"] = row["project_id"]
            if ident_in:
                _merge_json(rsi_dir / "identity.json", ident_in)
            if manifest_in:
                _merge_json(rsi_dir / "manifest.json", manifest_in)
            report = {
                "source_db": str(db_path),
                "finished_at": _utc_now(),
                "unmapped_retrieved": unmapped,
                "tables": table_counts,
            }
            _write_yaml(rsi_dir / "memory_migrate.yaml", report)
        except MemoryMigrateHalfError:
            raise
        except Exception as exc:
            _append_error(rsi_dir, exc, loc)
            raise MemoryMigrateHalfError(t("MIGRATE_HALF", loc)) from exc

    if progress is not None:
        progress.phase(t("MIGRATE_PHASE_BAK", loc))
    if not dry_run:
        try:
            bak = _rename_legacy_db(rsi_dir)
        except Exception as exc:
            _append_error(rsi_dir, exc, loc)
            raise MemoryMigrateHalfError(t("MIGRATE_HALF", loc)) from exc

    if progress is not None:
        progress.phase(t("MIGRATE_PHASE_INDEX", loc))
    if not dry_run:
        try:
            _write_index(rsi_dir, written_docs)
        except Exception as exc:
            _append_error(rsi_dir, exc, loc)

    if progress is not None:
        progress.phase(t("MIGRATE_PHASE_INJECT", loc))
    if not dry_run:
        try:
            injector = RuleInjector(store=MemoryStore(rsi_dir), project_root=root)
            await injector.rewrite()
        except Exception as exc:
            _append_error(rsi_dir, exc, loc)

    counts = {
        "knowledge": len(knowledge_pairs),
        "logs": len(events),
        "arms": len(arms),
        "unmapped": unmapped,
        "bak": bak,
        "tables": table_counts,
        "dry_run": dry_run,
    }
    if progress is not None:
        extra = t("MIGRATE_DONE_DRY", loc) if dry_run else ""
        progress.finish(
            extra,
            summary=t(
                "MIGRATE_SUMMARY",
                loc,
                knowledge=counts["knowledge"],
                logs=counts["logs"],
                arms=counts["arms"],
                unmapped=unmapped,
                bak=bak or "rsi.db",
            ),
            lang=loc,
        )
    return counts
