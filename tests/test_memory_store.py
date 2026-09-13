import asyncio
import json
import re
from pathlib import Path

import pytest
import yaml

from rsi_boot.core.masking import mask_text
from rsi_boot.memory.paths import official_dir, pending_dir
from rsi_boot.memory.store import MemoryStore
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.ux.messages import t


def test_write_then_read(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc = MemoryDoc(id="a"*32, type="prohibition", title="禁止 SELECT *", content="必须列字段")
    dest = official_dir(store.rsi_dir, "prohibition") / "no-star--aaaaaaaa.yaml"
    store.write(doc, dest=dest)
    got = store.read("a"*32)
    assert got.title == "禁止 SELECT *"
    assert got.status == "active"


def test_same_id_teaching_pair_prefers_case(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    tid = "c" * 32
    case = MemoryDoc(id=tid, type="teaching_case", title="t", content="case body")
    gene = MemoryDoc(id=tid, type="gene_case", title="t", content="index")
    store.write(case, dest=official_dir(store.rsi_dir, "teaching_case") / "t--cccccccc.yaml")
    store.write(gene, dest=official_dir(store.rsi_dir, "gene_case") / "manual" / "t--cccccccc.yaml")
    assert store.read(tid).type == "teaching_case"


def test_write_uses_dest_tmp_then_replace(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc = MemoryDoc(id="a" * 32, type="prohibition", title="禁止 SELECT *", content="必须列字段")
    dest = official_dir(store.rsi_dir, "prohibition") / "no-star--aaaaaaaa.yaml"
    store.write(doc, dest=dest)
    assert dest.is_file()
    assert not Path(str(dest) + ".tmp").exists()
    dumped = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert dumped["id"] == "a" * 32
    assert dumped["title"] == "禁止 SELECT *"


def test_unknown_top_level_fields_rejected_and_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "en")
    store = MemoryStore(tmp_path / ".rsi")
    dest = official_dir(store.rsi_dir, "prohibition") / "bad--bbbbbbbb.yaml"
    dest.parent.mkdir(parents=True)
    dest.write_text(
        "id: " + "b" * 32 + "\n"
        "type: prohibition\n"
        "title: t\n"
        "content: c\n"
        "unknown_field: nope\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=re.escape(t("YAML_INVALID", "en", path=str(dest)))):
        store.read("b" * 32)
    log = store.rsi_dir / "logs" / "errors.jsonl"
    assert log.is_file()
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert record["message"] == t("YAML_INVALID", "en", path=str(dest))
    assert "unknown_field" in record.get("error", "")


def test_write_masks_title_content_payload(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    raw_title = "联系 admin@example.com"
    raw_content = "服务器 203.0.113.9 宕机"
    raw_note = "password: hunter2"
    doc = MemoryDoc(
        id="e" * 32,
        type="convention",
        title=raw_title,
        content=raw_content,
        payload={"note": raw_note, "nested": {"ip": "10.0.0.8"}},
    )
    dest = official_dir(store.rsi_dir, "convention") / "mask--eeeeeeee.yaml"
    got = store.write(doc, dest=dest)
    assert got.title == mask_text(raw_title)
    assert got.content == mask_text(raw_content)
    assert got.payload["note"] == mask_text(raw_note)
    assert got.payload["nested"]["ip"] == mask_text("10.0.0.8")
    on_disk = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert on_disk["title"] == mask_text(raw_title)
    assert on_disk["content"] == mask_text(raw_content)
    assert on_disk["payload"]["note"] == mask_text(raw_note)
    assert "admin@example.com" not in dest.read_text(encoding="utf-8")
    assert "hunter2" not in dest.read_text(encoding="utf-8")


def test_list_official_and_pending(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    official = MemoryDoc(id="a" * 32, type="prohibition", title="off", content="o")
    pending = MemoryDoc(id="b" * 32, type="prohibition", title="pend", content="p")
    conv = MemoryDoc(id="d" * 32, type="convention", title="conv", content="c")
    store.write(official, dest=official_dir(store.rsi_dir, "prohibition") / "off--aaaaaaaa.yaml")
    store.write(pending, dest=pending_dir(store.rsi_dir, "prohibition") / "pend--bbbbbbbb.yaml")
    store.write(conv, dest=official_dir(store.rsi_dir, "convention") / "conv--dddddddd.yaml")

    all_official = store.list_official()
    assert {d.id for d in all_official} == {"a" * 32, "d" * 32}
    only_proh = store.list_official("prohibition")
    assert [d.id for d in only_proh] == ["a" * 32]
    assert only_proh[0].status == "active"

    pending_docs = store.list_pending()
    assert [d.id for d in pending_docs] == ["b" * 32]
    assert pending_docs[0].status == "pending_review"
    assert store.list_pending("convention") == []


def test_move_rewrites_status_and_slug_id8_name(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    doc = MemoryDoc(id="f" * 32, type="prohibition", title="Hello World!", content="must")
    src = pending_dir(store.rsi_dir, "prohibition") / "legacy-name.yaml"
    store.write(doc, dest=src)
    dest_dir = official_dir(store.rsi_dir, "prohibition")
    moved = store.move("f" * 32, dest_dir)
    assert moved.status == "active"
    assert moved.path.endswith("hello-world--ffffffff.yaml")
    assert (dest_dir / "hello-world--ffffffff.yaml").is_file()
    assert not src.exists()
    assert store.read("f" * 32).status == "active"


def test_read_missing_raises(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    with pytest.raises(FileNotFoundError):
        store.read("a" * 32)


def test_store_lock_is_asyncio_lock(tmp_path):
    store = MemoryStore(tmp_path / ".rsi")
    assert isinstance(store._lock, asyncio.Lock)
