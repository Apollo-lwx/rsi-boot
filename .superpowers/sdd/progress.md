# SDD Progress — readable-memory RAG DAG

Plan: docs/superpowers/plans/2026-09-13-readable-memory-rag-dag.md
Spec: docs/superpowers/specs/2026-09-13-readable-memory-rag-dag-design.md
Branch: feat/readable-memory-rag-dag
Worktree: d:\project\study\rsi-boot-wt-readable-memory
Baseline commit (pre-Task-1): 7c6a484

## Tasks

- [x] Task 1: complete (commits 7c6a484..adb0efc, review clean; Minor: mid-phase Progress copy still zh)
- [x] Task 2: complete (commits adb0efc..d237a81, review clean; Minor: body alias not skill-gated, description optional)
- [x] Task 3: complete (commits d237a81..b957a95, review Approved; Important carried: asyncio.Lock unused, real mutex is RLock — catalog/cache must take both)
- [x] Task 4: complete (commits b957a95..0b097cf, review clean after roll fix)
- [x] Task 5: complete (commits 0b097cf..c7a8fd3, review clean)
- [x] Task 6: complete (commits c7a8fd3..49ad829, review clean)
- [x] Task 7: complete (commits 49ad829..453d50c, review clean)
- [x] Task 8: complete (commits 453d50c..28b9bda, review clean after review-gate fix)
- [x] Task 9: complete (commits 28b9bda..1b0d54f, review Approved)
- [x] Task 10: complete (commits 1b0d54f..501cae2, review Approved after dest fix)
- [x] Task 11a: complete (commits 501cae2..427e07c, review Approved after log/review fixes)
- [x] Task 11b: complete (commits 427e07c..8f52de2, review Approved; Minor: leftover db= imports)
- [x] Task 12: complete (commits 8f52de2..45be1f6, review Approved after redact/code fix)
- [x] Task 13: complete (commits 45be1f6..bff4b70, review Approved)
- [x] Task 14: complete (commits bff4b70..a6b85fd, review Approved after wipe-tree test)
- [x] Task 15: complete (commits a6b85fd..a803632, review Approved after signature/path fix)
- [x] Task 16: complete (commits a803632..97a6fc7, review Approved after graph/closeout/≥3 fix)

## Minor findings (final review)

- Task 1: mid-phase Progress copy still zh (已用/约剩)
- Task 2: body alias not skill-gated; skill description optional; type/status unconstrained str
- Task 3: asyncio.Lock unused (sync API uses RLock); store imports injector.slug; read does not rewrite conflicting on-disk status
- Task 4: retrieved=None still accepted; no fsync on roll append-copy
- Task 5: English paraphrase test still shares raw token `select`; source-scan only covers retriever.py
- Task 6: 32KB trim untested; modified not counted as adoption
- Task 7: prescribed test does not lock items pipeline; 0.6 threshold no-op on IDF scores
- Task 15: unknown audit_* still PHASE_AUDIT copy; extract manifest not atomic; episode gates (count≥2, rejected+comment) untested; Top5 overlap is Jaccard-on-union
- Task 16: leftover PHASE_PROMOTE/GRAPH copy; scope=domain blankets 禁止* without applies_to global; validation==passed counts toward ≥3; no positive domain+global test; promote does not invalidate_index

## Final-review fix

Fixed the three Important findings from the whole-branch review (new commit, did not amend 97a6fc7):

1. File-runtime `FeedbackWorker._process_store` now calls `extract_from_feedback` for rejected+comment and modified (pipeline thresholds). Removed the candidate-only jsonl writer for those actions. `KnowledgeExtractor.run_daily` still no-ops when `_db is None`.
2. `--watch` `YamlWatcher.on_change` invalidates the index and schedules `injector.rewrite(project_id)` via `on_memory_yaml_change`.
3. `list_tools` uses each module's `TOOL_DESCRIPTION` from bilingual `TOOL_DESC`. Added keys for feedback/add/search/delete/harness/stats/conflicts. Localized `query 必填` / feedback validation errors on the touched tools.

Report: `.superpowers/sdd/final-review-fix-report.md`
