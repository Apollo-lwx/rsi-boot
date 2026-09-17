# RSI Boot

[简体中文](README.md) | English

A local memory layer for host models (Cursor, etc.). It distills conventions, prohibitions, and repair lessons that have been verified in conversations, injects them into rule files after review, and recalls them before the next task. Zero API keys — the main path makes no model calls; all data lives in the current repo's `.rsi/`.

## Installation

Requires Python 3.10+. From the root of this repository:

```bash
pip install -e .
rsi init
```

`rsi init` only writes the user-level config `~/.rsi/config.yaml`. Project memory lives in each repo's `.rsi/`, created on the first MCP startup or bootstrap run.

## Hooking up Cursor

In the `.cursor/mcp.json` of **the project you want memory for**, add:

```json
{
  "mcpServers": {
    "rsi-boot": {
      "command": "rsi",
      "args": ["serve"],
      "env": {
        "RSI_PROJECT_ROOT": "${workspaceFolder}"
      }
    }
  }
}
```

Then go to Settings → MCP and confirm `rsi-boot` is enabled. Reload the MCP after changing this repository's code.

Do **not** configure a user-level `mcp.json`: `${workspaceFolder}` in `args` often goes unexpanded there, which leaks memory across repos. Workspace binding uses only the `RSI_PROJECT_ROOT` env var, or the `WORKSPACE_FOLDER_PATHS` / `CURSOR_WORKSPACE_ROOT` variables injected by Cursor.

## Learning in conversation

Once the MCP is connected, don't run `rsi` in a terminal yourself — just talk to the agent in the project window.

| You say | The agent does |
|---------|----------------|
| Learn this project for the first time | `rsi bootstrap --consent --host-judge` → `rsi_learn` distills all pending packs → `rsi_knowledge_review` approves the run |
| Re-learn | `rsi wipe --yes` to clear memory first, then repeat the above |

If you see "must specify `--host-judge` or `--local-judge`", rerun with `--host-judge` — do not switch to `--local-judge` (that one is for terminal-only capture). `rsi wipe --yes` deletes `memory/`, `logs/`, `cache/`, `audit/`, `state/` and `manifest.json` under the project's `.rsi`, keeping `identity.json`; if files are locked, disable `rsi-boot` in Settings → MCP first, then retry.

After learning, check the lane list in `.rsi/bootstrap_report.md`: recall is usable only when pending=0 and the run has been approved via `rsi_knowledge_review`. Approved conventions enter recall only — they are not written out as `rsi-convention-*.mdc` rule files.

## CLI smoke test

For driving the CLI directly, outside of conversation. Writes require either `--host-judge` or `--local-judge`; `--dry-run` is exempt.

```bash
rsi recall "review this Python code for me"
rsi knowledge add --title "project convention" --content "this project uses snake_case everywhere"
rsi recall "what are the naming conventions in this project"

rsi bootstrap --dry-run
rsi bootstrap --local-judge
rsi bootstrap --local-judge --force

# migrate a legacy .rsi/rsi.db first
rsi memory migrate

# normally launched by Cursor; no need to run by hand
rsi serve
```

## MCP tools

| Tool | What it does |
|------|--------------|
| `rsi_recall` | Pre-task recall: prohibitions first, plus conventions / docs / gene / teaching |
| `rsi_feedback` | Adoption feedback; rejected+comment is distilled into a prohibition |
| `rsi_learn` | Reading packs (pack_list / pack_open / pack_done), teach_catch / teach_record, session audit |
| `rsi_knowledge_add` / `rsi_knowledge_search` / `rsi_knowledge_delete` / `rsi_knowledge_review` | Memory management and review; distilled entries must carry a `pack_id` |
| `rsi_conflicts` | Query and adjudicate conflicts between short knowledge items |
| `rsi_memory` | Memory index / open / rebuild / relationship graph |
| `rsi_review` | Harness improvement proposals and config snapshots |
| `rsi_stats` | Memory usage statistics |

RSI has no OAuth. Do not call `mcp_auth` mid-distillation, and never pop an authorization prompt at the user.

## Configuration

Config chain: in-package `default.yaml` → `~/.rsi/config.yaml` → project-root `rsi-boot.yaml` → request parameters.

`RSI_HOME` only overrides the user config directory (default `~/.rsi`). Project memory, profiles, and archives all live in the working directory's `.rsi/` — open another repo and you get another copy; nothing leaks across. The main path works with zero configuration; optional enhancements (vector retrieval, LLM extraction, etc., requiring your own model key) are documented in the `~/.rsi/config.yaml` example.

Plaintext keys are forbidden in a project's `rsi-boot.yaml` — the server refuses to start if one is found. Logs are redacted before being stored; content passes an approval gate and a blacklist before injection into host context; logs older than 90 days are automatically archived to `.rsi/archive/YYYYMM.jsonl`.

## Development

```bash
pip install -e ".[dev]"
python -m pytest
python scripts/export_schemas.py
```
