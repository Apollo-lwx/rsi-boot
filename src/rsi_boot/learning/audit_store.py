"""Audit session files under .rsi/audit/<scope>/; RSI never execs probes.

决策日志三维（借鉴 OpenMausBot decision log）：
- actor：每次 audit_start 生成 session_id 写入会话文件，审计/审批记录带
  actor（宿主标识 RSI_HOST 环境变量，缺省 unknown + 会话标识）；
- 保留窗：prune_audit_files 把超 archive_after_days（默认 30 天）的审计文件
  按月滚入 audit/_months/YYYY-MM/，超 retention_days（默认 180 天，项目
  rsi-boot.yaml 的 audit.retention_days 可配）删除；
- 导出：export_audit_csv 产出 CSV，单元格公式注入中和（= + - @ 开头加 '），
  内容过 mask_text 脱敏。
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from rsi_boot.memory.store import MemoryStore
from rsi_boot.ux.messages import t

_UNSAFE_SLUG = re.compile(r"[^0-9A-Za-z._-]+")

#: 决策日志保留窗与按月滚动阈值（项目 rsi-boot.yaml 的 audit.* 可覆盖）
DEFAULT_RETENTION_DAYS = 180
DEFAULT_ARCHIVE_AFTER_DAYS = 30

#: CSV 公式注入中和：这些字符开头的单元格加 ' 前缀（Excel/Sheets 防注入惯例）
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _atomic_write(dest: Path, text: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(dest) + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(dest)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _safe_slug(value: str, fallback: str = "scope") -> str:
    text = str(value).replace("\\", "/").strip()
    parts = [p for p in text.split("/") if p and p not in {".", ".."}]
    slug = _UNSAFE_SLUG.sub("-", "-".join(parts)).strip(".-")
    return slug or fallback


def _scope_name(arguments: dict[str, Any], lang: str) -> tuple[str | None, dict[str, Any] | None]:
    scope = str(arguments.get("scope") or "session").strip() or "session"
    if scope not in {"session", "full"}:
        return None, {"status": "error", "code": "invalid", "message": t("AUDIT_INVALID_SCOPE", lang)}
    scope_id = str(arguments.get("scope_id") or "").strip()
    if scope == "full" and not scope_id:
        return None, {"status": "error", "code": "invalid", "message": t("AUDIT_NEED_SCOPE_ID", lang)}
    folder = _safe_slug(scope_id, fallback="scope") if scope == "full" else "session"
    return folder, None


def _audit_dir(store: MemoryStore, folder: str) -> Path:
    dest = store.rsi_dir / "audit" / _safe_slug(folder)
    root = (store.rsi_dir / "audit").resolve()
    resolved = dest.resolve()
    if resolved != root and root not in resolved.parents:
        return store.rsi_dir / "audit" / "scope"
    return dest


def _session_path(store: MemoryStore) -> Path:
    return store.rsi_dir / "state" / "audit-session.yaml"


def _read_session(store: MemoryStore) -> dict[str, Any]:
    path = _session_path(store)
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _write_session(store: MemoryStore, data: dict[str, Any]) -> None:
    _atomic_write(
        _session_path(store),
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
    )


def resolve_host() -> str:
    """宿主标识：RSI_HOST 环境变量显式声明，缺省 unknown（MCP clientInfo 接入留后续）。"""
    return os.environ.get("RSI_HOST", "").strip() or "unknown"


def _actor(store: MemoryStore) -> dict[str, str]:
    """决策日志 actor：宿主标识 + 会话标识（audit_start 生成的 session_id）。"""
    session = _read_session(store)
    return {
        "host": resolve_host(),
        "session_id": str(session.get("session_id") or ""),
    }


def audit_start(store: MemoryStore, arguments: dict[str, Any], lang: str) -> dict[str, Any]:
    folder, err = _scope_name(arguments, lang)
    if err:
        return err
    assert folder is not None
    files = arguments.get("changed_files") or arguments.get("files") or []
    globs = arguments.get("globs") or arguments.get("glob") or []
    if isinstance(globs, str):
        globs = [globs]
    if not isinstance(files, list):
        files = []
    if not isinstance(globs, list):
        globs = []
    stamp = _utc_stamp()
    session = _read_session(store)
    reopened = bool(session.get("opened_at")) and not session.get("closed_at")
    previous_scope = str(session.get("scope_id") or "") if reopened else ""
    session.update({
        "scope": str(arguments.get("scope") or "session"),
        "scope_id": folder,
        "opened_at": _utc_now(),
        "session_id": uuid.uuid4().hex[:12],  # 决策日志会话标识：每次 audit_start 一轮新会话
    })
    session.pop("closed_at", None)
    _write_session(store, session)
    dest = _audit_dir(store, folder) / f"ats-{stamp}.json"
    import json

    _atomic_write(
        dest,
        json.dumps(
            {"changed_files": files, "globs": globs, "scope": folder, "actor": _actor(store)},
            ensure_ascii=False,
        ),
    )
    rel = dest.relative_to(store.rsi_dir).as_posix()
    data: dict[str, Any] = {"scope": folder, "path": rel, "actor": _actor(store)}
    # 中断语义：上次会话未 audit_finish（进程中断/宿主退出）不自动丢弃，显式告知宿主
    if reopened:
        data["reopened_unclosed"] = True
        data["previous_scope"] = previous_scope
    return {
        "status": "success",
        "message": t("AUDIT_STARTED", lang, scope=folder),
        "data": data,
    }


def audit_probes(store: MemoryStore, arguments: dict[str, Any], lang: str) -> dict[str, Any]:
    folder, err = _scope_name(arguments, lang)
    if err:
        return err
    assert folder is not None
    results = arguments.get("probe_results")
    if not isinstance(results, list):
        return {"status": "error", "code": "invalid", "message": t("AUDIT_NEED_PROBES", lang)}
    import json

    dest = _audit_dir(store, folder) / f"probe-{_utc_stamp()}.json"
    _atomic_write(
        dest,
        json.dumps({"probe_results": results, "actor": _actor(store)}, ensure_ascii=False),
    )
    rel = dest.relative_to(store.rsi_dir).as_posix()
    return {
        "status": "success",
        "message": t("AUDIT_PROBES_SAVED", lang, n=len(results)),
        "data": {"path": rel, "n": len(results)},
    }


def audit_report(store: MemoryStore, arguments: dict[str, Any], lang: str) -> dict[str, Any]:
    folder, err = _scope_name(arguments, lang)
    if err:
        return err
    assert folder is not None
    overall = str(arguments.get("overall") or "").strip()
    if overall not in {"pass", "fail"}:
        return {"status": "error", "code": "invalid", "message": t("AUDIT_NEED_OVERALL", lang)}
    scope = str(arguments.get("scope") or "session").strip() or "session"
    findings = arguments.get("findings")
    if not isinstance(findings, list):
        findings = []
    probe_summary = arguments.get("probe_summary")
    if not isinstance(probe_summary, dict):
        probe_summary = {}
    disclaimer = arguments.get("disclaimer_partial")
    if scope == "session" and overall == "pass":
        disclaimer = True
    elif disclaimer is None:
        disclaimer = False
    body = {
        "scope": scope,
        "overall": overall,
        "findings": findings,
        "probe_summary": probe_summary,
        "disclaimer_partial": bool(disclaimer),
        "actor": _actor(store),
    }
    dest = _audit_dir(store, folder) / f"audit-{_utc_stamp()}.yaml"
    _atomic_write(dest, yaml.safe_dump(body, allow_unicode=True, sort_keys=False))
    rel = dest.relative_to(store.rsi_dir).as_posix()
    return {
        "status": "success",
        "message": t("AUDIT_REPORTED", lang, path=rel),
        "data": {"path": rel, **body},
    }


def audit_finish(store: MemoryStore, arguments: dict[str, Any], lang: str) -> dict[str, Any]:
    folder, err = _scope_name(arguments, lang)
    if err:
        return err
    session = _read_session(store)
    session["scope"] = str(arguments.get("scope") or session.get("scope") or "session")
    if folder:
        session["scope_id"] = folder
    session["closed_at"] = _utc_now()
    _write_session(store, session)
    return {
        "status": "success",
        "message": t("AUDIT_FINISHED", lang),
        "data": {"closed_at": session["closed_at"], "path": "state/audit-session.yaml"},
    }


# ---------- 按月滚动 + 保留窗 ----------


def _audit_windows(rsi_dir: Path, retention_days: int | None, archive_after_days: int | None) -> tuple[int, int]:
    """从项目配置读 audit.retention_days / audit.archive_after_days，缺省 180/30。"""
    retention, archive_after = retention_days, archive_after_days
    if retention is None or archive_after is None:
        cfg: dict[str, Any] = {}
        try:
            from ..config.loader import load_config

            loaded = load_config(Path(rsi_dir).parent)
            cfg = loaded if isinstance(loaded, dict) else {}
        except Exception:
            cfg = {}
        audit_cfg = cfg.get("audit") if isinstance(cfg.get("audit"), dict) else {}
        if retention is None:
            retention = int(audit_cfg.get("retention_days") or DEFAULT_RETENTION_DAYS)
        if archive_after is None:
            archive_after = int(audit_cfg.get("archive_after_days") or DEFAULT_ARCHIVE_AFTER_DAYS)
    return retention, archive_after


def prune_audit_files(
    rsi_dir: Path,
    retention_days: int | None = None,
    archive_after_days: int | None = None,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """决策日志生命周期：超保留窗删除，超滚动阈值按月归档到 audit/_months/YYYY-MM/。

    dry_run=True 只计数不落盘。返回 {"archived": n, "pruned": n}。
    单文件异常跳过不中断（清理是旁路不是闸）。
    """
    import logging

    logger = logging.getLogger(__name__)
    rsi_dir = Path(rsi_dir)
    audit_root = rsi_dir / "audit"
    if not audit_root.is_dir():
        return {"archived": 0, "pruned": 0}
    retention, archive_after = _audit_windows(rsi_dir, retention_days, archive_after_days)
    now = now or datetime.now(timezone.utc)
    archived = pruned = 0
    for path in sorted(audit_root.rglob("*")):
        if not path.is_file():
            continue
        in_months = "_months" in path.relative_to(audit_root).parts
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        age_days = (now - mtime).total_seconds() / 86400
        try:
            if age_days > retention:
                if not dry_run:
                    path.unlink()
                pruned += 1
            elif age_days > archive_after and not in_months:
                if not dry_run:
                    month_dir = audit_root / "_months" / mtime.strftime("%Y-%m")
                    month_dir.mkdir(parents=True, exist_ok=True)
                    dest = month_dir / path.name
                    if dest.exists():
                        dest = month_dir / f"{path.stem}-{uuid.uuid4().hex[:6]}{path.suffix}"
                    path.replace(dest)
                archived += 1
        except OSError:
            logger.warning("审计文件清理跳过（单文件异常）: %s", path, exc_info=True)
    return {"archived": archived, "pruned": pruned}


# ---------- 导出（CSV 公式注入中和 + 脱敏） ----------


def _csv_safe(text: Any) -> str:
    """单元格中和：换行/回车压平防串行；= + - @ 等开头加 ' 防公式注入；过 mask_text 脱敏。"""
    from ..core.masking import mask_text

    safe = mask_text(str(text)).replace("\r", " ").replace("\n", " ").strip()
    if safe.startswith(_FORMULA_PREFIXES):
        safe = "'" + safe
    return safe


def export_audit_csv(rsi_dir: Path, dest: Path) -> dict[str, Any]:
    """导出决策日志为 CSV：审计报告（audit-*.yaml）+ 审批事件（events kind=review）。

    列：ts, kind, scope, actor_host, actor_session, verdict, detail。
    所有文本单元格过 _csv_safe（公式注入中和 + 脱敏）。
    """
    import csv
    import json

    rsi_dir = Path(rsi_dir)
    rows: list[dict[str, str]] = []

    audit_root = rsi_dir / "audit"
    if audit_root.is_dir():
        for path in sorted(audit_root.rglob("audit-*.yaml")):
            try:
                body = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(body, dict):
                continue
            actor = body.get("actor") if isinstance(body.get("actor"), dict) else {}
            findings = body.get("findings") if isinstance(body.get("findings"), list) else []
            finding_brief = json.dumps(findings, ensure_ascii=False)[:200] if findings else ""
            detail = path.relative_to(rsi_dir).as_posix()
            if finding_brief:
                detail = f"{detail} | findings: {finding_brief}"
            rows.append({
                "ts": _stamp_of(path.name),
                "kind": "audit",
                "scope": _csv_safe(body.get("scope") or ""),
                "actor_host": _csv_safe(actor.get("host") or ""),
                "actor_session": _csv_safe(actor.get("session_id") or ""),
                "verdict": _csv_safe(body.get("overall") or ""),
                "detail": _csv_safe(detail),
            })

    events_path = rsi_dir / "logs" / "events.jsonl"
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("kind") != "review":
                continue
            actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
            ids = ",".join(str(i) for i in (event.get("ids") or [])[:20])
            rows.append({
                "ts": _csv_safe(event.get("ts") or ""),
                "kind": "review",
                "scope": "",
                "actor_host": _csv_safe(actor.get("host") or ""),
                "actor_session": "",
                "verdict": _csv_safe(event.get("action") or ""),
                "detail": _csv_safe(f"processed={event.get('processed', '')} ids={ids}"),
            })

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["ts", "kind", "scope", "actor_host", "actor_session", "verdict", "detail"],
        )
        writer.writeheader()
        writer.writerows(rows)
    return {"path": str(dest), "rows": len(rows)}


def _stamp_of(filename: str) -> str:
    """audit-YYYYMMDDTHHMMSSZ.yaml → 时间戳串；解析失败回退文件名。"""
    stem = filename.removeprefix("audit-").removesuffix(".yaml")
    return stem or filename
