"""把按包域写错类型的蒸馏条挪到 skill / teaching_case / gene_case / pattern。"""

from __future__ import annotations

from pathlib import Path

from rsi_boot.injector.slug import slugify
from rsi_boot.memory.paths import official_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.scanner.reading_packs import distill_type_for_domain, load_pack

_RETYPE_TYPES = frozenset({"skill", "teaching_case", "gene_case", "pattern"})


def _skill_name(doc: MemoryDoc) -> str:
    name = str((doc.payload or {}).get("name") or "").strip()
    if name:
        return slugify(name)
    title = str(doc.title or "").strip()
    if title.lower().startswith("skill "):
        title = title[6:].strip()
    return slugify(title)


def _dest_for(store: MemoryStore, doc: MemoryDoc, typ: str) -> Path:
    if typ == "skill":
        return official_dir(store.rsi_dir, "skill") / _skill_name(doc) / "skill.yaml"
    return official_dir(store.rsi_dir, typ) / memory_filename(doc.title, doc.id)


def _relocate(store: MemoryStore, doc: MemoryDoc, dest: Path) -> MemoryDoc:
    src = store.rsi_dir / doc.path if doc.path else None
    written = store.write(doc, dest=dest)
    if src is not None and src.exists() and src.resolve() != dest.resolve():
        src.unlink()
        store._index_forget(src)
    return written


def retype_distilled_for_pack(store: MemoryStore, pack_id: str) -> int:
    """按阅读包域改类型并搬家。只处理 signal:distilled。返回改写条数。"""
    pack = load_pack(store.rsi_dir, str(pack_id or "").strip())
    if pack is None:
        return 0
    want = distill_type_for_domain(str(pack.get("domain") or ""))
    if want not in _RETYPE_TYPES:
        return 0
    marker = f"pack_id:{pack['id']}"
    n = 0
    docs = list(store.list_official(skip_harvest=True)) + list(store.list_pending(skip_harvest=True))
    for doc in docs:
        tags = set(doc.tags or [])
        if marker not in tags or "signal:distilled" not in tags:
            continue
        if doc.type == want and doc.path and official_dir(store.rsi_dir, want).as_posix() in Path(doc.path).as_posix().replace("\\", "/"):
            if want != "skill" or str(doc.path).replace("\\", "/").endswith("/skill.yaml"):
                continue
        payload = dict(doc.payload or {})
        if want == "skill":
            payload["name"] = _skill_name(doc)
            if not payload.get("description"):
                payload["description"] = (doc.description or doc.content or "")[:400]
        updated = doc.model_copy(update={"type": want, "payload": payload})
        if want == "skill" and not updated.description:
            updated = updated.model_copy(update={"description": payload.get("description") or ""})
        _relocate(store, updated, _dest_for(store, updated, want))
        n += 1
    return n


def retype_dense_pack_items(store: MemoryStore) -> dict[str, int]:
    """skills / teaching / gene-map / patterns 包各跑一遍。"""
    out: dict[str, int] = {}
    from rsi_boot.scanner.reading_packs import load_index

    for item in load_index(store.rsi_dir).get("packs") or []:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "")
        if distill_type_for_domain(domain) not in _RETYPE_TYPES:
            continue
        pack_id = str(item.get("id") or "")
        if pack_id:
            out[pack_id] = retype_distilled_for_pack(store, pack_id)
    return out
