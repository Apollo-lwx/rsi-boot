import yaml

from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag.index import build_index, search, tokenize
from rsi_boot.rag.query import expand_query


def test_paraphrase_hits_without_exact_title():
    docs = [MemoryDoc(
        id="b"*32, type="convention",
        title="禁止 SELECT *", content="查询必须显式列出列名，禁止星号",
    )]
    idx = build_index(docs)
    expanded, intent, _ = expand_query("别把所有列一次查出来")
    assert expanded != "禁止 SELECT *"
    hits = search(idx, expanded, top_n=5)
    assert hits and hits[0][0] == "b"*32


def test_english_paraphrase_hits_chinese_doc():
    docs = [MemoryDoc(id="c"*32, type="convention",
                      title="禁止 SELECT *", content="必须写列名")]
    idx = build_index(docs)
    expanded, _, _ = expand_query("don't select star from the table")
    hits = search(idx, expanded, top_n=5)
    assert any(doc_id == "c"*32 for doc_id, _ in hits)


def test_expand_query_uses_rsi_dir_terms_not_cwd(tmp_path, monkeypatch):
    rsi = tmp_path / "proj" / ".rsi"
    (rsi / "state").mkdir(parents=True)
    (rsi / "state" / "terms.yaml").write_text(
        yaml.safe_dump({"synonyms": {"小抄": ["cheat-sheet"]}}, allow_unicode=True),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    expanded, _, _ = expand_query("看一下小抄", rsi_dir=rsi)
    assert "cheat-sheet" in expanded
    cwd_only, _, _ = expand_query("看一下小抄")
    assert "cheat-sheet" not in cwd_only


def test_expand_query_namespaces_intent_so_explain_is_not_a_search_token():
    expanded, intent, _ = expand_query("解释 Kerberos 和 SPNEGO 怎么发 Authorization")
    assert intent == "explain"
    assert "intent:explain" in expanded
    assert "explain" not in tokenize(expanded)


def test_search_drops_sql_explain_and_jdbc_only_when_query_has_rarer_terms():
    docs = [
        MemoryDoc(
            id="a" * 32, type="convention",
            title="AuthenticationType KERBEROS equals SPNEGO",
            content="Remote JDBC client KERBEROS and SPNEGO send Authorization Negotiate",
        ),
        MemoryDoc(
            id="b" * 32, type="convention",
            title="GaussDB EXPLAIN dual track",
            content="EXPLAIN ANALYZE is utility. EXPLAIN without ANALYZE stays SqlExplain",
        ),
        MemoryDoc(
            id="c" * 32, type="convention",
            title="Keep derived aliases out of JDBC column cache",
            content="JDBC cache must ignore derived alias columns",
        ),
        MemoryDoc(
            id="d" * 32, type="gene_case",
            title="dm dual skip jdbc metadata",
            content="DUAL treated as a physical table; JDBC getColumns fails closed",
        ),
        MemoryDoc(
            id="e" * 32, type="teaching_case",
            title="kerberos is real account login",
            content="Kerberos and SPNEGO are physical datasource accounts",
        ),
        MemoryDoc(
            id="f" * 32, type="teaching_case",
            title="Teaching: DM tree uses JDBC getSchemas",
            content="Dameng object-tree inventory must call official JDBC getSchemas",
        ),
    ]
    idx = build_index(docs)
    expanded, _, _ = expand_query(
        "解释远端 JDBC 客户端上 Kerberos 与 SPNEGO 是否一回事，以及 Authorization 头如何发出",
    )
    item_ids = [doc_id for doc_id, _ in search(idx, expanded, types={"convention"}, top_n=5)]
    assert "a" * 32 in item_ids
    assert "b" * 32 not in item_ids
    assert "c" * 32 not in item_ids
    gene_ids = [doc_id for doc_id, _ in search(idx, expanded, types={"gene_case"}, top_n=5)]
    assert gene_ids == []
    teach_ids = [doc_id for doc_id, _ in search(idx, expanded, types={"teaching_case"}, top_n=5)]
    assert "e" * 32 in teach_ids
    assert "f" * 32 not in teach_ids


def test_chinese_paraphrase_still_hits_gene_without_english_select():
    docs = [
        MemoryDoc(
            id="1" * 32, type="gene_case",
            title="自动基因",
            content="查询必须显式列出列名，禁止星号",
        ),
        MemoryDoc(
            id="2" * 32, type="teaching_case",
            title="列出列名",
            content="必须写列名，禁止星号 SELECT *",
        ),
    ]
    idx = build_index(docs)
    expanded, _, _ = expand_query("别把所有列一次查出来")
    gene_ids = [doc_id for doc_id, _ in search(idx, expanded, types={"gene_case"}, top_n=5)]
    teach_ids = [doc_id for doc_id, _ in search(idx, expanded, types={"teaching_case"}, top_n=5)]
    assert "1" * 32 in gene_ids
    assert "2" * 32 in teach_ids


def test_user_typed_explain_can_still_hit_sql_explain_docs():
    docs = [
        MemoryDoc(
            id="b" * 32, type="convention",
            title="GaussDB EXPLAIN dual track",
            content="EXPLAIN ANALYZE is utility passthrough",
        ),
    ]
    idx = build_index(docs)
    expanded, _, _ = expand_query("explain how GaussDB EXPLAIN ANALYZE is routed")
    hits = search(idx, expanded, types={"convention"}, top_n=5)
    assert hits and hits[0][0] == "b" * 32
