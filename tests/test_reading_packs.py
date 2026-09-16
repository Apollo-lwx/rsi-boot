"""Reading-pack index / fingerprint IO (host-distill-knowledge Wave A Task 2)."""

from __future__ import annotations

from pathlib import Path

import yaml

from rsi_boot.memory.store import MemoryStore
from rsi_boot.scanner.reading_packs import (
    PACKS_DIRNAME,
    MAX_SOURCES_PER_PACK,
    build_packs,
    distill_type_for_domain,
    distill_type_for_pack,
    pack_lane,
    load_index,
    load_pack,
    pack_fingerprint,
    packs_dir,
    set_pack_status,
    skip_allowed,
    source_fingerprint,
    unlink_host_judge_queue,
    write_packs,
    write_relearn_skill,
)


def _src(**kwargs) -> dict:
    base = {"kind": "docs", "path": "docs/auth.md", "heading": "认证"}
    base.update(kwargs)
    return base


def _pack(pack_id: str = "auth", *, sources: list[dict] | None = None, **kwargs) -> dict:
    item = {
        "id": pack_id,
        "domain": "auth",
        "title": "认证与会话",
        "sources": sources if sources is not None else [_src()],
    }
    item.update(kwargs)
    return item


def test_write_packs_creates_index_yaml(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    n = write_packs(rsi_dir, bootstrap_run_id="run-1", packs=[_pack()])
    assert n == 1
    index_path = rsi_dir / "state" / PACKS_DIRNAME / "index.yaml"
    assert index_path.is_file()
    data = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    assert data["bootstrap_run_id"] == "run-1"
    assert data["packs"][0]["id"] == "auth"
    assert data["packs"][0]["status"] == "pending"
    assert data["packs"][0]["source_count"] == 1
    assert data["packs"][0]["fingerprint"]
    pack = load_pack(rsi_dir, "auth")
    assert pack is not None
    assert pack["id"] == "auth"
    assert pack["sources"][0]["kind"] == "docs"
    assert "content" not in pack["sources"][0]


def test_same_fingerprint_keeps_done(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    write_packs(rsi_dir, bootstrap_run_id="run-1", packs=[_pack()])
    set_pack_status(rsi_dir, "auth", "done")
    write_packs(rsi_dir, bootstrap_run_id="run-2", packs=[_pack()])
    index = load_index(rsi_dir)
    assert index["packs"][0]["status"] == "done"
    assert load_pack(rsi_dir, "auth")["status"] == "done"


def test_changed_source_fingerprint_resets_done_to_pending(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    write_packs(rsi_dir, bootstrap_run_id="run-1", packs=[_pack()])
    set_pack_status(rsi_dir, "auth", "done")
    changed = _pack(sources=[_src(heading="会话")])
    write_packs(rsi_dir, bootstrap_run_id="run-2", packs=[changed])
    assert load_index(rsi_dir)["packs"][0]["status"] == "pending"
    assert load_pack(rsi_dir, "auth")["status"] == "pending"


def test_force_resets_done_to_pending(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    write_packs(rsi_dir, bootstrap_run_id="run-1", packs=[_pack()])
    set_pack_status(rsi_dir, "auth", "done")
    write_packs(rsi_dir, bootstrap_run_id="run-2", packs=[_pack()], force=True)
    assert load_index(rsi_dir)["packs"][0]["status"] == "pending"
    assert load_pack(rsi_dir, "auth")["status"] == "pending"


def test_set_pack_status_unknown_id_is_not_found(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    write_packs(rsi_dir, bootstrap_run_id="run-1", packs=[_pack()])
    result = set_pack_status(rsi_dir, "missing", "done")
    assert result == {"status": "error", "code": "not_found"}


def test_set_pack_status_same_terminal_is_idempotent(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    write_packs(rsi_dir, bootstrap_run_id="run-1", packs=[_pack()])
    first = set_pack_status(rsi_dir, "auth", "done", reason="ok")
    assert first["status"] != "error"
    second = set_pack_status(rsi_dir, "auth", "done")
    assert second["status"] != "error"
    assert load_pack(rsi_dir, "auth")["status"] == "done"


def test_unlink_host_judge_queue_deletes_file(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    rsi_dir.mkdir()
    queue = rsi_dir / "host_judge_queue.json"
    queue.write_text("[]", encoding="utf-8")
    unlink_host_judge_queue(rsi_dir)
    assert not queue.exists()
    unlink_host_judge_queue(rsi_dir)


def test_load_index_missing_dir_is_empty(tmp_path: Path):
    assert load_index(tmp_path / ".rsi") == {"packs": []}


def test_source_and_pack_fingerprint_are_stable_sha256():
    a = source_fingerprint("docs", path="docs/a.md", heading="A")
    b = source_fingerprint("docs", path="docs/a.md", heading="B")
    assert len(a) == 64
    assert a != b
    sources = [
        {"kind": "docs", "path": "docs/b.md", "heading": "B"},
        {"kind": "docs", "path": "docs/a.md", "heading": "A"},
    ]
    assert pack_fingerprint(sources) == pack_fingerprint(list(reversed(sources)))
    assert pack_fingerprint(sources) != pack_fingerprint(sources[:1])


def test_packs_dir_is_under_state(tmp_path: Path):
    rsi_dir = tmp_path / ".rsi"
    assert packs_dir(rsi_dir) == rsi_dir / "state" / "reading-packs"


def test_write_packs_does_not_accept_omitted_sources_kw(tmp_path: Path):
    import inspect

    sig = inspect.signature(write_packs)
    assert "omitted_sources" not in sig.parameters
    assert "omitted" not in sig.parameters


def _build(**overrides):
    kwargs = {
        "docs": [],
        "code": [],
        "git_fix": [],
        "rules": [],
        "skills": [],
        "conversations": [],
    }
    kwargs.update(overrides)
    return build_packs(**kwargs)


def _all_sources(packs: list[dict]) -> list[dict]:
    return [src for pack in packs for src in pack["sources"]]


def test_pack_lane_splits_parallel_boards():
    assert pack_lane("src/auth") == "code"
    assert pack_lane("skills") == "skills_rules"
    assert pack_lane("rules") == "skills_rules"
    assert pack_lane("docs") == "docs"
    assert pack_lane("README") == "docs"
    assert pack_lane("git-fix") == "git"
    assert pack_lane("teaching") == "teaching"
    assert pack_lane(".cursor") == "cursor"
    assert pack_lane("conversation") == "conversation"
    assert pack_lane("misc") == "other"
    assert distill_type_for_domain("skills") == "skill"
    assert distill_type_for_domain("teaching") == "teaching_case"
    assert distill_type_for_domain("gene-map") == "gene_case"
    assert distill_type_for_domain("patterns") == "pattern"
    assert distill_type_for_domain("src/auth") == "convention"
    assert distill_type_for_domain("docs") == "documentation"


def test_forty_one_docs_same_dir_split_into_at_least_two_packs():
    docs = [{"path": f"docs/page-{i:02d}.md", "heading": f"H{i}"} for i in range(41)]
    packs, omitted = _build(docs=docs)
    assert omitted == []
    assert len(packs) >= 2
    assert all(len(pack["sources"]) <= MAX_SOURCES_PER_PACK for pack in packs)
    assert sum(len(pack["sources"]) for pack in packs) == 41
    paths = [src["path"] for src in _all_sources(packs)]
    assert len(paths) == len(set(paths)) == 41


def test_same_path_appears_in_only_one_pack():
    packs, _ = _build(
        docs=[
            {"path": "docs/auth.md", "heading": "认证"},
            {"path": "docs/auth.md", "heading": "重复"},
        ],
        rules=[{"path": "docs/auth.md", "heading": "规则侧"}],
    )
    paths = [src.get("path") for src in _all_sources(packs) if src.get("path")]
    assert paths.count("docs/auth.md") == 1


def test_eighty_one_singleton_dirs_all_stay_packed():
    docs = [{"path": f"dir{i:03d}/doc.md", "heading": f"H{i}"} for i in range(81)]
    packs, omitted = _build(docs=docs)
    assert omitted == []
    assert len(packs) == 81
    kept = {src.get("path") for src in _all_sources(packs)}
    assert kept == {doc["path"] for doc in docs}


def test_docs_code_and_git_fix_keep_packing_past_eighty():
    # 各域独立顶层目录，避免同域 ≤40 条被收成一包后看不出「停切」
    docs = [{"path": f"docs-area-{i:03d}/guide.md", "heading": f"D{i}"} for i in range(50)]
    code = [{"path": f"src/mod{i:03d}/Api.java", "heading": f"C{i}"} for i in range(50)]
    git_fix = [
        {"hash": f"{i:07x}", "message": f"fix {i}", "files": [f"orphan/{i}.c"]}
        for i in range(40)
    ]
    packs, omitted = _build(docs=docs, code=code, git_fix=git_fix)
    assert omitted == []
    assert len(packs) > 80
    kinds = {src["kind"] for src in _all_sources(packs)}
    assert kinds == {"docs", "code", "git_fix"}
    assert {d["path"] for d in docs} <= {s.get("path") for s in _all_sources(packs)}
    assert {c["path"] for c in code} <= {s.get("path") for s in _all_sources(packs)}
    assert {g["hash"] for g in git_fix} <= {s.get("hash") for s in _all_sources(packs)}


def test_git_fix_identity_is_hash_code_path_still_allowed():
    code_path = "src/auth/Token.java"
    packs, omitted = _build(
        code=[{"path": code_path, "heading": "Token"}],
        git_fix=[{
            "hash": "abc1234",
            "message": "fix token refresh NPE",
            "files": [code_path],
        }],
    )
    assert omitted == []
    sources = _all_sources(packs)
    paths = [src.get("path") for src in sources if src.get("path")]
    hashes = [src.get("hash") for src in sources if src.get("hash")]
    assert paths.count(code_path) == 1
    assert hashes.count("abc1234") == 1
    code_src = next(src for src in sources if src.get("kind") == "code")
    git_src = next(src for src in sources if src.get("kind") == "git_fix")
    assert code_src["path"] == code_path
    assert git_src["hash"] == "abc1234"
    assert "path" not in git_src or git_src.get("path") in (None, "")
    code_pack = next(
        pack for pack in packs
        if any(src.get("kind") == "code" and src.get("path") == code_path for src in pack["sources"])
    )
    assert any(src.get("hash") == "abc1234" for src in code_pack["sources"])


def test_git_fix_unmatched_files_use_git_fix_domain():
    packs, _ = _build(
        git_fix=[{
            "hash": "def5678",
            "message": "fix orphan",
            "files": ["vendor/legacy.c"],
        }],
    )
    assert any(pack["domain"] == "git-fix" for pack in packs)
    git_src = next(src for src in _all_sources(packs) if src.get("kind") == "git_fix")
    assert git_src["hash"] == "def5678"
    assert git_src.get("files") == ["vendor/legacy.c"]


def test_domains_follow_first_level_and_kind_rules():
    packs, _ = _build(
        docs=[
            {"path": "docs/auth.md", "heading": "认证"},
            {"path": "README.md", "heading": "简介"},
        ],
        code=[{"path": "src/auth/Token.java", "heading": "Token"}],
        rules=[{"path": ".cursor/rules/foo.mdc", "heading": "Foo"}],
        skills=[{"path": ".cursor/skills/bar/SKILL.md", "name": "bar"}],
        conversations=[
            {"path": ".cursor/notes.md"},
            {"path": "agent-transcripts/foo.jsonl"},
        ],
    )
    domains = {pack["domain"] for pack in packs}
    assert "rules" in domains
    assert "skills" in domains
    assert "docs" in domains
    assert any(pack["domain"] == "README" for pack in packs)
    assert any(pack["domain"] in {"src/auth", "src"} for pack in packs)
    assert "conversation" in domains
    assert ".cursor" in domains
    kinds = {src["kind"] for src in _all_sources(packs)}
    assert kinds == {"docs", "code", "rule", "skill", "conversation"}


def test_version_hints_set_source_hint():
    packs, _ = _build(
        docs=[{"path": "docs/api-v1.md", "heading": "API"}],
        version_hints={"docs/api-v1.md": "version-family"},
    )
    src = _all_sources(packs)[0]
    assert src["path"] == "docs/api-v1.md"
    assert src["hint"] == "version-family"


def test_pack_ids_are_unique_stable_slug_or_hex():
    import re

    docs = [{"path": f"docs/page-{i:02d}.md", "heading": f"H{i}"} for i in range(3)]
    packs_a, _ = _build(docs=docs)
    packs_b, _ = _build(docs=docs)
    ids_a = [pack["id"] for pack in packs_a]
    ids_b = [pack["id"] for pack in packs_b]
    assert ids_a == ids_b
    assert len(ids_a) == len(set(ids_a))
    for pack_id in ids_a:
        assert len(pack_id) >= 8
        assert re.fullmatch(r"[A-Za-z0-9._-]+", pack_id)


def test_teaching_and_gene_kept_when_readme_would_fill_cap():
    teaching = [
        {"path": f".cursor/knowledge/teaching-cases/case-{i:02d}.md", "heading": f"T{i}"}
        for i in range(12)
    ]
    readmes = [
        {"path": f"docs/engine-{i:02d}/README.md", "heading": f"R{i}"}
        for i in range(80)
    ]
    packs, omitted = _build(docs=teaching + readmes)
    kept = {src.get("path") for src in _all_sources(packs)}
    assert all(src["path"] in kept for src in teaching)
    readme_packs = [p for p in packs if p["domain"] == "README"]
    assert len(readme_packs) <= 2
    assert not any("teaching-cases" in (path or "") for path in omitted)


def test_scattered_readmes_chunk_instead_of_one_pack_each():
    readmes = [
        {"path": f"module-{i:02d}/README.md", "heading": f"R{i}"}
        for i in range(41)
    ]
    packs, omitted = _build(docs=readmes)
    assert omitted == []
    readme_packs = [p for p in packs if p["domain"] == "README"]
    assert len(readme_packs) == 2
    assert sum(len(p["sources"]) for p in readme_packs) == 41


def test_large_doc_set_is_fully_packed():
    docs = [{"path": f"d{i:04d}/x.md", "heading": "H"} for i in range(80 + 250)]
    packs, omitted = _build(docs=docs)
    assert omitted == []
    assert len(packs) == 330
    assert {d["path"] for d in docs} == {s.get("path") for s in _all_sources(packs)}


def test_skip_allowed_only_readme_or_rsi_audit_dirs():
    assert skip_allowed({
        "domain": "src/ats-catalog",
        "sources": [{"path": "src/ats-catalog/Probe.java"}],
    }) is False
    assert skip_allowed({
        "domain": "conversation",
        "sources": [{"path": ".cursor/knowledge/gene-map/cases/audit-optype-hive.yaml"}],
    }) is False
    assert skip_allowed({
        "domain": ".cursor",
        "sources": [{"path": ".cursor/audit-result/_session/ats-20260827.json"}],
    }) is True
    assert skip_allowed({
        "domain": "README",
        "sources": [{"path": "docs/dialects/mysql/README.md"}],
    }) is True


def test_conversation_gene_map_routes_to_teaching_lane():
    packs, _ = _build(conversations=[
        {"path": ".cursor/knowledge/gene-map/cases/audit-optype-hive.yaml"},
        {"path": "agent-transcripts/done-example.jsonl"},
    ])
    domains = {pack["domain"] for pack in packs}
    assert "gene-map" in domains
    assert "conversation" in domains
    assert distill_type_for_pack({
        "domain": "conversation",
        "sources": [{"path": ".cursor/knowledge/gene-map/cases/audit-optype-hive.yaml"}],
    }) == "gene_case"


def test_write_relearn_skill_upserts_official_yaml(tmp_path: Path):
    store = MemoryStore(tmp_path / ".rsi")
    assert write_relearn_skill(store) is True
    skills = store.list_official("skill")
    match = [d for d in skills if (d.payload or {}).get("name") == "rsi-relearn"]
    assert len(match) == 1
    assert match[0].status == "active"
    assert "pack_list" in match[0].content
    assert "并行" in match[0].content
    assert "本轮必须把全部 pending 蒸完" in match[0].content
    assert "只有 README 文件名或路径落在" in match[0].content
    assert ".cursor/audit-result" in match[0].content
    assert "禁止" in match[0].content and "mcp_auth" in match[0].content
    assert "rsi bootstrap --consent --host-judge" in match[0].content
    assert "Cursor" not in match[0].content
    dest = tmp_path / ".rsi" / "memory" / "skills"
    assert dest.is_dir()
    assert not (tmp_path / ".cursor" / "skills" / "rsi-relearn").exists()
    assert write_relearn_skill(store) is True
    again = [d for d in store.list_official("skill") if (d.payload or {}).get("name") == "rsi-relearn"]
    assert len(again) == 1
    assert again[0].id == match[0].id
