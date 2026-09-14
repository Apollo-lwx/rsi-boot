"""Atomic YAML MemoryStore: dest.tmp → os.replace, same-id teaching pair."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import ValidationError

from rsi_boot.core.masking import mask_text
from rsi_boot.injector.slug import slugify
from rsi_boot.memory.paths import official_dir, pending_dir
from rsi_boot.memory.types import MEMORY_TYPES, MemoryDoc, status_from_path
from rsi_boot.ux.lang import locale_lang
from rsi_boot.ux.messages import t

_PENDING_TYPES = ("prohibition", "convention", "skill")


def memory_filename(title: str, id: str) -> str:
    """`{slug}--{id8}.yaml` — claim is by in-file id, not the name."""
    return f"{slugify(title)}--{id[:8]}.yaml"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(v) for v in value]
    return value


def _atomic_replace(dest: Path, text: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(dest) + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        last_err: OSError | None = None
        for attempt in range(8):
            try:
                os.replace(tmp, dest)
                return
            except OSError as exc:
                last_err = exc
                time.sleep(0.05 * (attempt + 1))
        if last_err is not None:
            raise last_err
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


class MemoryStore:
    def __init__(self, rsi_dir: Path):
        self.rsi_dir = Path(rsi_dir)
        self._lock = asyncio.Lock()
        self._io = threading.RLock()

    def write(self, doc: MemoryDoc, *, dest: Path) -> MemoryDoc:
        """写 dest.tmp → os.replace；回写 status 与 path；返回 doc。"""
        with self._io:
            return self._write_unlocked(doc, dest)

    def read(self, id: str) -> MemoryDoc:
        """同 id 教学对：优先 teaching-cases。找不到 raise FileNotFoundError。"""
        with self._io:
            return self._read_unlocked(id)

    def move(self, id: str, dest_dir: Path) -> MemoryDoc:
        with self._io:
            doc = self._read_unlocked(id)
            src = self.rsi_dir / doc.path if doc.path else None
            dest = Path(dest_dir) / memory_filename(doc.title, doc.id)
            written = self._write_unlocked(doc, dest)
            if src is not None and src.exists() and src.resolve() != dest.resolve():
                src.unlink()
            return written

    def list_official(self, type: str | None = None) -> list[MemoryDoc]:
        types: Iterable[str] = (type,) if type else MEMORY_TYPES
        with self._io:
            docs: list[MemoryDoc] = []
            for typ in types:
                docs.extend(self._load_tree(official_dir(self.rsi_dir, typ)))
            return docs

    def list_pending(self, type: str | None = None) -> list[MemoryDoc]:
        types: Iterable[str] = (type,) if type else _PENDING_TYPES
        with self._io:
            docs: list[MemoryDoc] = []
            for typ in types:
                docs.extend(self._load_tree(pending_dir(self.rsi_dir, typ)))
            return docs

    def list_all(self) -> list[MemoryDoc]:
        """Official + pending + archive (and any other memory/**/*.yaml)."""
        with self._io:
            return self._load_tree(self.rsi_dir / "memory")

    def _write_unlocked(self, doc: MemoryDoc, dest: Path) -> MemoryDoc:
        dest = Path(dest)
        root = self.rsi_dir.resolve()
        resolved = dest.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(t("YAML_INVALID", locale_lang(), path=str(dest))) from exc
        now = _utc_now()
        rel = self._rel_posix(dest)
        masked = doc.model_copy(
            update={
                "title": mask_text(doc.title),
                "content": mask_text(doc.content),
                "payload": _mask_value(doc.payload),
                "status": status_from_path(rel),
                "path": rel,
                "created_at": doc.created_at or now,
                "updated_at": now,
            }
        )
        payload = masked.model_dump(exclude_none=True)
        text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        _atomic_replace(dest, text)
        return masked

    def _read_unlocked(self, id: str) -> MemoryDoc:
        matches: list[MemoryDoc] = []
        invalid: list[Path] = []
        for path in self._iter_memory_yaml():
            raw = self._safe_load(path)
            if not isinstance(raw, dict):
                if raw is not None:
                    self._log_invalid(path)
                continue
            if raw.get("id") != id:
                continue
            try:
                matches.append(self._hydrate(raw, path))
            except ValidationError as exc:
                self._log_invalid(path, exc)
                invalid.append(path)
        if matches:
            for doc in matches:
                if doc.type == "teaching_case":
                    return doc
            return matches[0]
        if invalid:
            path = invalid[0]
            raise ValueError(t("YAML_INVALID", locale_lang(), path=str(path)))
        raise FileNotFoundError(t("NOT_FOUND", locale_lang(), id=id))

    def _load_tree(self, root: Path) -> list[MemoryDoc]:
        docs: list[MemoryDoc] = []
        if not root.exists():
            return docs
        for path in self._iter_yaml(root):
            raw = self._safe_load(path)
            if not isinstance(raw, dict):
                if raw is not None:
                    self._log_invalid(path)
                continue
            try:
                docs.append(self._hydrate(raw, path))
            except ValidationError as exc:
                self._log_invalid(path, exc)
        return docs

    def _hydrate(self, raw: dict, path: Path) -> MemoryDoc:
        doc = MemoryDoc.model_validate(raw)
        rel = self._rel_posix(path)
        return doc.model_copy(update={"status": status_from_path(rel), "path": rel})

    def _rel_posix(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.rsi_dir.resolve()).as_posix()
        except ValueError:
            return path.as_posix().replace("\\", "/")

    def _iter_memory_yaml(self) -> list[Path]:
        return self._iter_yaml(self.rsi_dir / "memory")

    def _iter_yaml(self, root: Path) -> list[Path]:
        if not root.exists():
            return []
        return sorted(
            p for p in root.rglob("*.yaml") if not p.name.endswith(".tmp")
        )

    def _safe_load(self, path: Path) -> Any:
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            self._log_invalid(path, exc)
            return None

    def _log_invalid(self, path: Path, exc: BaseException | None = None) -> None:
        log = self.rsi_dir / "logs" / "errors.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        message = t("YAML_INVALID", locale_lang(), path=str(path))
        record = {
            "ts": _utc_now(),
            "kind": "yaml_invalid",
            "path": str(path),
            "message": message,
        }
        if exc is not None:
            record["error"] = str(exc)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
