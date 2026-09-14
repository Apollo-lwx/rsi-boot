# Final-review fix report

Worktree: `d:\project\study\rsi-boot-wt-readable-memory`  
Base: `97a6fc7` (not amended)  
Date: 2026-09-14

## Fixes

1. **extract_from_feedback on file runtime** — `FeedbackWorker._process_store` calls `rsi_boot.learning.pipeline.extract_from_feedback` with the recall event’s `retrieved` ids for `rejected`+comment and `modified`. Removed `_enqueue_candidate_store` (pipeline already appends `candidates.jsonl`).
2. **`--watch` rewrites inject** — `on_memory_yaml_change` calls `invalidate_index()` then `asyncio.create_task(injector.rewrite(project_id))`. Wired from `run_serve` YamlWatcher `on_change`.
3. **list_tools via TOOL_DESC** — Each registered tool uses `TOOL_DESCRIPTION = TOOL_DESC[...]`. New bilingual keys: `feedback`, `knowledge_add`, `knowledge_search`, `knowledge_delete`, `harness`, `stats`, `conflicts`. No `rsi_query`. Error strings `QUERY_REQUIRED` / `FEEDBACK_ACTION_INVALID` / `FEEDBACK_RATING_RANGE` on touched tools.

## Commands + output

### Targeted

```
$env:PYTHONPATH='d:\project\study\rsi-boot-wt-readable-memory\src'
python -X utf8 -m pytest tests/test_extract_pending_yaml.py tests/test_watch_yaml.py tests/test_runtime_no_sqlite.py tests/test_feedback_worker.py tests/test_ux_messages.py -q
```

```
..........................                                               [100%]
26 passed in 1.48s
```

(First RED run of new tests failed as expected: no pending YAML, missing `on_memory_yaml_change`, missing `TOOL_DESC["feedback"]`.)

### Full suite (once)

```
$env:PYTHONPATH='d:\project\study\rsi-boot-wt-readable-memory\src'
python -X utf8 -m pytest tests -q
```

```
..s..................................................................... [ 10%]
........................................................................ [ 21%]
........................................................................ [ 32%]
........................................................................ [ 43%]
........................................................................ [ 53%]
........................................................................ [ 64%]
........................................................................ [ 75%]
........................................................................ [ 86%]
........................................................................ [ 96%]
.....................                                                    [100%]
668 passed, 1 skipped in 53.01s
```

Exit code 0.
