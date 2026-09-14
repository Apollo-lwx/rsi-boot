"""Audit session files under .rsi/audit/<scope>/; RSI never execs probes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from rsi_boot.memory.store import MemoryStore
from rsi_boot.ux.messages import t


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


def _scope_name(arguments: dict[str, Any], lang: str) -> tuple[str | None, dict[str, Any] | None]:
    scope = str(arguments.get("scope") or "session").strip() or "session"
    if scope not in {"session", "full"}:
        return None, {"status": "error", "code": "invalid", "message": t("AUDIT_INVALID_SCOPE", lang)}
    scope_id = str(arguments.get("scope_id") or "").strip()
    if scope == "full" and not scope_id:
        return None, {"status": "error", "code": "invalid", "message": t("AUDIT_NEED_SCOPE_ID", lang)}
    return (scope_id if scope == "full" else "session"), None


def _audit_dir(store: MemoryStore, folder: str) -> Path:
    return store.rsi_dir / "audit" / folder


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
    dest = _audit_dir(store, folder) / f"ats-{stamp}.json"
    import json

    _atomic_write(
        dest,
        json.dumps({"changed_files": files, "globs": globs, "scope": folder}, ensure_ascii=False),
    )
    session = _read_session(store)
    session.update({
        "scope": str(arguments.get("scope") or "session"),
        "scope_id": folder,
        "opened_at": _utc_now(),
    })
    session.pop("closed_at", None)
    _write_session(store, session)
    rel = dest.relative_to(store.rsi_dir).as_posix()
    return {
        "status": "success",
        "message": t("AUDIT_STARTED", lang, scope=folder),
        "data": {"scope": folder, "path": rel},
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
    _atomic_write(dest, json.dumps({"probe_results": results}, ensure_ascii=False))
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
