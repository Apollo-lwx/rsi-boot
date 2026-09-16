import uuid
from pathlib import Path

import pytest

from rsi_boot.learning.reading_pack_actions import pack_done, pack_list, pack_open
from rsi_boot.memory.paths import review_dir
from rsi_boot.memory.store import MemoryStore, memory_filename
from rsi_boot.memory.types import MemoryDoc
from rsi_boot.scanner.reading_packs import write_packs
from rsi_boot.ux.messages import t


def _add_distilled(store: MemoryStore, pack_id: str, n: int = 1) -> None:
    for i in range(n):
        doc_id = uuid.uuid4().hex
        doc = MemoryDoc(
            id=doc_id,
            type="documentation",
            title=f"蒸馏条 {pack_id} {i}",
            content="自包含短知识正文用于过 pack_done 门槛",
            tags=["signal:distilled", f"pack_id:{pack_id}", "bootstrap_run_id:run-1"],
        )
        dest = review_dir(store.rsi_dir, "documentation") / memory_filename(doc.title, doc.id)
        store.write(doc, dest=dest)


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / ".rsi")


def _seed(store: MemoryStore, packs: list[dict]) -> None:
    write_packs(store.rsi_dir, bootstrap_run_id="run-1", packs=packs)


def test_pack_list_empty_dir_does_not_raise(tmp_path):
    store = _store(tmp_path)
    (store.rsi_dir / "memory" / "documentation").mkdir(parents=True)
    (store.rsi_dir / "memory" / "documentation" / "ghost.yaml").write_text("id: x\n", encoding="utf-8")
    out = pack_list(store, "zh")
    assert out["status"] == "success"
    assert out["data"] == {
        "total": 0,
        "done": 0,
        "pending": 0,
        "skipped": 0,
        "packs": [],
        "lanes": [],
    }
    assert out["message"] == t("PACK_NEED_BOOTSTRAP", "zh")
    assert out["next"]["recommended"] == "1"
    assert out["next"]["options"][0]["action"] == "bootstrap"


def test_pack_open_unknown_is_not_found(tmp_path):
    store = _store(tmp_path)
    _seed(store, [{
        "id": "auth",
        "domain": "auth",
        "title": "认证",
        "sources": [{"kind": "docs", "path": "docs/a.md", "heading": "A"}],
    }])
    out = pack_open(store, "missing", "zh")
    assert out == {
        "status": "error",
        "code": "not_found",
        "message": t("PACK_NOT_FOUND", "zh"),
    }


def test_pack_done_is_idempotent(tmp_path):
    store = _store(tmp_path)
    _seed(store, [{
        "id": "auth",
        "domain": "auth",
        "title": "认证",
        "sources": [{"kind": "docs", "path": "docs/a.md", "heading": "A"}],
    }])
    empty = pack_done(store, "auth", status="done", lang="zh")
    assert empty["status"] == "error"
    assert empty["code"] == "need_items"
    _add_distilled(store, "auth")
    first = pack_done(store, "auth", status="done", lang="zh")
    assert first["status"] == "success"
    assert first["pack_status"] == "done"
    second = pack_done(store, "auth", status="done", lang="zh")
    assert second["status"] == "success"
    assert second["pack_status"] == "done"
    listed = pack_list(store, "zh")
    assert listed["data"]["done"] == 1
    assert listed["data"]["pending"] == 0


def test_pack_done_skills_require_one_item_per_source(tmp_path):
    store = _store(tmp_path)
    _seed(store, [{
        "id": "skills-0000",
        "domain": "skills",
        "title": "skills",
        "sources": [
            {"kind": "skill", "path": f"s{i}/SKILL.md", "name": f"s{i}"}
            for i in range(3)
        ],
    }])
    _add_distilled(store, "skills-0000", n=1)
    out = pack_done(store, "skills-0000", status="done", lang="zh")
    assert out["status"] == "error"
    assert out["code"] == "need_items"
    _add_distilled(store, "skills-0000", n=2)
    ok = pack_done(store, "skills-0000", status="done", lang="zh")
    assert ok["status"] == "success"


def test_pack_list_counts_statuses(tmp_path):
    store = _store(tmp_path)
    _seed(store, [
        {
            "id": "a",
            "domain": "a",
            "title": "A",
            "sources": [{"kind": "docs", "path": "a.md", "heading": "A"}],
        },
        {
            "id": "b",
            "domain": "README",
            "title": "B",
            "sources": [{"kind": "docs", "path": "README.md", "heading": "B"}],
        },
        {
            "id": "c",
            "domain": "c",
            "title": "C",
            "sources": [{"kind": "docs", "path": "c.md", "heading": "C"}],
        },
    ])
    _add_distilled(store, "a")
    pack_done(store, "a", status="done")
    pack_done(store, "b", status="skipped", reason="README install")
    out = pack_list(store, "zh")
    assert out["data"]["total"] == 3
    assert out["data"]["done"] == 1
    assert out["data"]["skipped"] == 1
    assert out["data"]["pending"] == 1
    by_id = {p["id"]: p for p in out["data"]["packs"]}
    assert by_id["a"]["status"] == "done"
    assert by_id["b"]["status"] == "skipped"
    assert by_id["c"]["status"] == "pending"
    opened = pack_open(store, "c", "zh")
    assert opened["status"] == "success"
    assert opened["data"]["id"] == "c"
    assert "content" not in opened["data"]["sources"][0]
    listed_next = {opt["action"]: opt for opt in out["next"]["options"]}
    assert listed_next["pack_open"]["args"]["pack_id"] == "c"
    assert out["next"]["recommended"] == "1"
    assert "wait" not in listed_next
    assert "先不蒸馏" not in out.get("message", "")
    assert out["data"]["lanes"]
    assert {lane["id"] for lane in out["data"]["lanes"]} == {"other", "docs"}
    assert opened["next"]["options"][0]["action"] == "pack_done"
    _add_distilled(store, "c")
    done_c = pack_done(store, "c", status="done", lang="zh")
    assert done_c["next"]["options"][0]["action"] == "knowledge_review"
    md = (store.rsi_dir / "bootstrap_report.md").read_text(encoding="utf-8")
    assert "## 下一步" not in md
    assert "done 2" in md
    assert "skipped 1" in md


def test_pack_done_skip_only_readme_or_audit(tmp_path):
    store = _store(tmp_path)
    _seed(store, [
        {
            "id": "conversation-0000",
            "domain": "conversation",
            "title": "对话",
            "sources": [{"kind": "conversation", "path": "a.jsonl"}],
        },
        {
            "id": "src-auth",
            "domain": "src/auth",
            "title": "Auth",
            "sources": [{"kind": "code", "path": "src/auth/A.java", "heading": "A"}],
        },
        {
            "id": "readme-0000",
            "domain": "README",
            "title": "Install",
            "sources": [{"kind": "docs", "path": "install/README.md", "heading": "Install"}],
        },
        {
            "id": "cursor-audit",
            "domain": ".cursor",
            "title": "Session audit",
            "sources": [{"kind": "docs", "path": ".cursor/audit-result/report.md", "heading": "Audit"}],
        },
    ])
    overview = pack_done(store, "conversation-0000", status="skipped", reason="overview", lang="zh")
    assert overview["status"] == "error"
    assert overview["code"] == "skip_not_allowed"
    mirror = pack_done(store, "src-auth", status="skipped", reason="version-mirror", lang="zh")
    assert mirror["status"] == "error"
    assert mirror["code"] == "skip_not_allowed"
    readme = pack_done(store, "readme-0000", status="skipped", reason="install", lang="zh")
    assert readme["status"] == "success"
    audit = pack_done(store, "cursor-audit", status="skipped", reason="one-off audit", lang="zh")
    assert audit["status"] == "success"


def test_pack_list_reopens_unallowed_skips(tmp_path):
    store = _store(tmp_path)
    _seed(store, [
        {
            "id": "conversation-0000",
            "domain": "conversation",
            "title": "对话",
            "sources": [{"kind": "conversation", "path": "a.jsonl"}],
        },
        {
            "id": "readme-0000",
            "domain": "README",
            "title": "Install",
            "sources": [{"kind": "docs", "path": "README.md", "heading": "Install"}],
        },
    ])
    pack_done(store, "readme-0000", status="skipped", reason="install", lang="zh")
    # 旧门槛留下的误 skip：直接改文件模拟
    from rsi_boot.scanner.reading_packs import set_pack_status
    set_pack_status(store.rsi_dir, "conversation-0000", "skipped", "overview")
    out = pack_list(store, "zh", reopen_skipped=True)
    assert out["data"]["reopened"] == 1
    by_id = {p["id"]: p for p in out["data"]["packs"]}
    assert by_id["conversation-0000"]["status"] == "pending"
    assert by_id["readme-0000"]["status"] == "skipped"


def test_skip_rejects_product_ats_and_gene_map_audit_name(tmp_path):
    store = _store(tmp_path)
    _seed(store, [
        {
            "id": "src-ats",
            "domain": "src/ats-catalog",
            "title": "ATS catalog",
            "sources": [{"kind": "code", "path": "src/ats-catalog/Probe.java", "heading": "Probe"}],
        },
        {
            "id": "conversation-gene",
            "domain": "conversation",
            "title": "gene-map case",
            "sources": [{
                "kind": "conversation",
                "path": ".cursor/knowledge/gene-map/cases/audit-optype-hive.yaml",
            }],
        },
    ])
    ats = pack_done(store, "src-ats", status="skipped", reason="ats layout", lang="zh")
    assert ats["status"] == "error"
    assert ats["code"] == "skip_not_allowed"
    gene = pack_done(store, "conversation-gene", status="skipped", reason="audit name", lang="zh")
    assert gene["status"] == "error"
    assert gene["code"] == "skip_not_allowed"


def test_pack_list_reopens_index_yaml_desync(tmp_path):
    store = _store(tmp_path)
    _seed(store, [{
        "id": "src-auth",
        "domain": "src/auth",
        "title": "Auth",
        "sources": [{"kind": "code", "path": "src/auth/A.java", "heading": "A"}],
    }])
    from rsi_boot.scanner.reading_packs import load_index, packs_dir
    import yaml
    pack_path = packs_dir(store.rsi_dir) / "src-auth.yaml"
    pack = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    pack["status"] = "pending"
    pack_path.write_text(yaml.safe_dump(pack, allow_unicode=True), encoding="utf-8")
    index_path = packs_dir(store.rsi_dir) / "index.yaml"
    index = load_index(store.rsi_dir)
    index["packs"][0]["status"] = "skipped"
    index["packs"][0]["reason"] = "version-mirror"
    index_path.write_text(yaml.safe_dump(index, allow_unicode=True, sort_keys=False), encoding="utf-8")
    out = pack_list(store, "zh", reopen_skipped=True)
    assert out["data"]["reopened"] == 1
    by_id = {p["id"]: p for p in out["data"]["packs"]}
    assert by_id["src-auth"]["status"] == "pending"


def test_pack_list_filters_lane_and_asks_to_finish(tmp_path):
    store = _store(tmp_path)
    _seed(store, [
        {
            "id": "docs-0000",
            "domain": "docs",
            "title": "Docs",
            "sources": [{"kind": "docs", "path": "docs/a.md", "heading": "A"}],
        },
        {
            "id": "src-auth",
            "domain": "src/auth",
            "title": "Auth",
            "sources": [{"kind": "code", "path": "src/auth/A.java", "heading": "A"}],
        },
        {
            "id": "skills-0000",
            "domain": "skills",
            "title": "Skills",
            "sources": [{"kind": "skill", "path": "s/SKILL.md", "name": "s"}],
        },
    ])
    listed = pack_list(store, "zh")
    assert listed["data"]["pending"] == 3
    assert "本轮必须蒸完剩余 3" in listed["message"]
    lane_ids = {lane["id"] for lane in listed["data"]["lanes"]}
    assert lane_ids == {"docs", "code", "skills_rules"}
    code = pack_list(store, "zh", lane="code")
    assert [p["id"] for p in code["data"]["packs"]] == ["src-auth"]
    assert code["data"]["lanes"][0]["id"] == "code"
    assert code["data"]["lanes"][0]["pending_ids"] == ["src-auth"]


@pytest.mark.asyncio
async def test_learn_tool_dispatches_pack_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LANG", "zh")
    from rsi_boot.api.tools import learn_tool
    from rsi_boot.bootstrap import build_runtime

    rt = await build_runtime(project_root=tmp_path)
    try:
        empty = await learn_tool.handle(rt, {"action": "pack_list"})
        assert empty["status"] == "success"
        assert empty["data"]["total"] == 0
        write_packs(rt.store.rsi_dir, bootstrap_run_id="run-1", packs=[{
            "id": "auth",
            "domain": "auth",
            "title": "认证",
            "sources": [{"kind": "docs", "path": "docs/a.md", "heading": "A"}],
        }])
        listed = await learn_tool.handle(rt, {"action": "pack_list"})
        assert listed["data"]["pending"] == 1
        opened = await learn_tool.handle(rt, {"action": "pack_open", "id": "auth"})
        assert opened["data"]["id"] == "auth"
        _add_distilled(rt.store, "auth")
        done = await learn_tool.handle(rt, {"action": "pack_done", "id": "auth"})
        assert done["pack_status"] == "done"
        missing = await learn_tool.handle(rt, {"action": "pack_open", "id": "nope"})
        assert missing["code"] == "not_found"
    finally:
        await rt.close()
