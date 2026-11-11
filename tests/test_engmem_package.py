"""EngMem-Bench HF 打包器与 PII 门禁测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGMEM = ROOT / "benchmark" / "engmem"
sys.path.insert(0, str(ENGMEM))

import package_hf  # noqa: E402
import pii_scan  # noqa: E402


@pytest.fixture(scope="module")
def dist(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("hf-dist") / "engmem-bench"
    monkey_out = package_hf.OUT
    package_hf.OUT = out
    try:
        package_hf.main()
    finally:
        package_hf.OUT = monkey_out
    return out


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestPackage:
    def test_files_exist(self, dist: Path):
        for name in ("README.md", "questions.jsonl", "corpus.jsonl",
                     "repos.json", "load_example.py"):
            assert (dist / name).is_file(), name

    def test_question_count_and_fields(self, dist: Path):
        questions = _jsonl(dist / "questions.jsonl")
        assert len(questions) == 315
        for q in questions:
            assert {"id", "repo", "type", "difficulty", "question",
                    "answer", "gold"} <= q.keys()
            assert q["type"] in ("convention", "prohibition", "fix")
            assert q["difficulty"] in ("easy", "medium", "hard")
            assert q["gold"], q["id"]

    def test_gold_ids_resolve(self, dist: Path):
        corpus_ids = {d["id"] for d in _jsonl(dist / "corpus.jsonl")}
        for q in _jsonl(dist / "questions.jsonl"):
            for g in q["gold"]:
                assert g in corpus_ids, f"{q['id']} 悬空 gold {g}"

    def test_corpus_fields_and_repos(self, dist: Path):
        corpus = _jsonl(dist / "corpus.jsonl")
        assert corpus, "语料不能为空"
        for d in corpus:
            assert {"id", "repo", "file", "section", "line",
                    "title", "content"} <= d.keys()
        repos = json.loads((dist / "repos.json").read_text(encoding="utf-8"))
        assert len(repos) == 21
        for r in repos:
            assert r["url"].startswith("https://github.com/")
            assert r["license"] and r["commit"]

    def test_card_frontmatter(self, dist: Path):
        card = (dist / "README.md").read_text(encoding="utf-8")
        assert card.startswith("---")
        assert "license: cc-by-4.0" in card
        assert "pretty_name: EngMem-Bench" in card

    def test_load_example_runs(self, dist: Path):
        import subprocess
        r = subprocess.run(
            [sys.executable, "-X", "utf8", str(dist / "load_example.py")],
            capture_output=True, timeout=60, encoding="utf-8",
        )
        assert r.returncode == 0, r.stderr
        assert "question:" in r.stdout


class TestPiiScan:
    def test_detects_secret_and_email_and_cjk(self):
        findings = pii_scan.scan_text(
            "key = sk-abcdef1234567890\n"
            "mail me at bob@example-corp.com\n"
            "路径里有中文\n",
            source="t",
            allowlist=set(),
        )
        kinds = {f.kind for f in findings}
        assert {"secret", "email", "cjk"} <= kinds

    def test_detects_personal_marker(self):
        findings = pii_scan.scan_text(
            "see gitee.com/many_money/x", source="t", allowlist=set()
        )
        assert any(f.kind == "personal" for f in findings)

    def test_detects_local_path(self):
        findings = pii_scan.scan_text(
            r"built at D:\project\study\rsi-boot", source="t", allowlist=set()
        )
        assert any(f.kind == "local_path" for f in findings)

    def test_allowlisted_email_passes(self):
        findings = pii_scan.scan_text(
            "contact security@gunicorn.org for issues",
            source="t",
            allowlist={"security@gunicorn.org"},
        )
        assert not findings

    def test_clean_text_passes(self):
        findings = pii_scan.scan_text(
            "Run the test suite locally before opening a pull request.",
            source="t",
            allowlist=set(),
        )
        assert not findings

    def test_real_allowlist_file_loads(self):
        allow = pii_scan.load_allowlist()
        assert "security@gunicorn.org" in allow
        assert all(not a.startswith("#") for a in allow)
