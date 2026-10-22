<div align="center">
  <img src="assets/logo.svg" alt="rsi-boot logo" width="120">

[![Install MCP Server](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en/install-mcp?name=rsi-boot&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJyc2ktYm9vdCIsInJzaSIsInNlcnZlIl19)
[![Install in VS Code](https://img.shields.io/badge/Install%20in-VS%20Code-0078d7?logo=visualstudiocode&logoColor=white)](vscode:mcp/install?%7B%22name%22%3A%22rsi-boot%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22rsi-boot%22%2C%22rsi%22%2C%22serve%22%5D%7D)
</div>

# RSI Boot

[简体中文](README.md) | English

Plenty of tools already store agent memory. RSI Boot is built for two other jobs: **a harness that improves as you use it**, and **memory that travels with the repo so switching hosts does not mean re-teaching the project**.

The main path needs no API key and makes no model calls. Conventions, prohibitions, repair lessons, and harness slots live only in the current repo's `.rsi/`, and are recalled on demand before the next task.

## Not another memory plugin

**rsi-harness (Harness-RSI)**
Negative feedback lands in failure buckets and only writable slots change (prompts / skills / recall strategy / intent rules / knowledge). A proposal must pass the static gate and human review before promotion; it is watched for 7 days and rolled back if adoption drops. The point is to get better at the work, not to hoard more notes.

**Follows the project**
Memory is isolated per repo — not per account, not per IDE. Copy the repo, move machines, or rename the directory, and `.rsi/` comes along. Open another repo and you get another copy; nothing leaks across. The harness genome can be exported as a bundle for backup or a slot move.

**Host-portable**
One MCP server (`rsi serve`) plugs into Cursor, Qoder, and Codex; the status line speaks both Claude Code and Cursor CLI. Injection writes `.cursor/rules` plus an `AGENTS.md` managed block that every host already reads, so you do not re-explain the project when you switch tools.

| | Host-native memory | Cloud memory (Mem0 and the like) | Generic local memory MCP | RSI Boot |
|---|---|---|---|---|
| Data follows | the account / the host | the cloud user | depends | **the current repo** |
| Switch Cursor / Claude Code / Codex | start over | only if that host is wired | usually store-and-recall | **the same `.rsi/`** |
| Harness self-improvement | no | no | weak or none | **propose → gate → observe → roll back** |
| API key on the main path | bound to the host | required | often | **none** |
| Write gate | the model decides | server-side policy | rare | **review + prohibitions + conflict rulings** |

## Installation

Requires Python 3.10+:

```bash
pip install rsi-boot
rsi init
```

To hack on the source instead, clone this repo and use the editable install described in [Development](#development).

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

Do **not** configure a user-level `mcp.json`: `${workspaceFolder}` in `args` often goes unexpanded there, which leaks memory across repos. Workspace binding order: `RSI_PROJECT_ROOT` (project-level mcp.json, recommended — explicit contract) → MCP `roots/list` (answered automatically by Cursor in plugin form) → the `WORKSPACE_FOLDER_PATHS` variable injected by Cursor → process cwd.

## Other hosts

### Claude Code

```bash
claude mcp add rsi-boot -- uvx --from rsi-boot rsi serve
```

One command and done (installed into the current project's scope; the workspace is claimed automatically via roots/cwd). See the status line section below for the Claude Code statusLine integration.

### Qoder

This repository is also a Qoder plugin: the `.qoder-plugin/plugin.json` manifest plus the root `mcp.json` (the same MCP config shared with the Cursor plugin) — zip it to distribute. To wire it up manually, either:

```bash
qoder mcp add rsi-boot -- rsi serve
```

or add the same `mcpServers` snippet as Cursor to a project-root `.mcp.json`. Workspace binding order is identical (`RSI_PROJECT_ROOT` → roots/list → injected variables → cwd).

### Codex

One-command setup (recommended):

```bash
codex mcp add rsi-boot -- uvx --from rsi-boot rsi serve
```

Or write TOML yourself: user-level `~/.codex/config.toml`, or project-level `.codex/config.toml` (loaded only from trusted directories):

```toml
[mcp_servers.rsi-boot]
command = "rsi"
args = ["serve"]
```

Just start codex from the project root — the MCP child process inherits its cwd and rsi-boot claims the workspace by walking up from there. To pin explicitly, add one line to the table: `env = { RSI_PROJECT_ROOT = "/absolute/path" }`.

## Status line (optional)

`rsi statusline` renders the host CLI's bottom status line: model, context headroom, token stats (in/out/cache), cost, today's RSI recall/adoption, plus a rotating quip. Works with both Claude Code and Cursor CLI — their `statusLine` hooks pipe a session JSON into stdin, and this command prints a single ANSI-colored line.

Claude Code (`~/.claude/settings.json`):

```json
{
  "statusLine": { "type": "command", "command": "rsi statusline" }
}
```

Cursor CLI (`~/.cursor/cli-config.json`):

```json
{
  "statusLine": { "type": "command", "command": "rsi statusline" }
}
```

Example output:

```
🧠 Kimi K3 │ 📊 ctx left 71% │ ⛽ in 12.3k · out 1.8k · cache 96k │ 💰 $0.42 │ 🐾 RSI recalls 3 · adopted 92% │ sending you a heart ❤
```

### Choosing a quip style

Three equivalent ways — pick per scenario (priority: `--style` > env var > project > user):

```jsonc
// ① Stays with the host: put the flag right in the statusLine command
{ "statusLine": { "type": "command", "command": "rsi statusline --style buddy" } }
```

```yaml
# ② Project level (per-repo style, shared across hosts): <workspace>/.rsi/config.yaml
# ③ User level (global default): ~/.rsi/config.yaml
statusline_style: buddy
```

Six presets: `mixed` (default), `buddy` (WorkBuddy-style companionship: company/reassurance/healing/blessings), `serious` (companionship and maxims only — no cats or fortune-telling), `healing`, `geek`, `lucky`. Takes effect on the next refresh — the file is re-read every time, no restart needed. The `lang` key in config.yaml follows the same priority chain.

Note: the status line is not an MCP server and is not governed by mcp.json (that's for the `rsi serve` memory service), so the style lives in the statusLine command or config.yaml only.

### Notes:

- Token details come from the transcript (Claude Code) or the `context_window` payload fields (Cursor CLI). The context window defaults to 200k, switches to 1M when `exceeds_200k_tokens` is set, and can be overridden via `RSI_STATUSLINE_CTX_WINDOW`.
- Three supply layers for quips: **user quips file** (`~/.rsi/quips.json` — any non-empty pool fully replaces its built-in counterpart) > handwritten pools (130+ entries, quality floor) + a template composer (~200 combinations, weighted by combination space). The user file is generated by the host model in conversation (just say "generate statusline quips"; the style spec is embedded in the file header) and verified with `rsi statusline --validate-quips`.
- Style filters by theme tags (core/motto/kudos/fortune/geek/dry/healing/food/cat/game/hype/slack/offwork/weather); situational pools (morning/late-night/context-low/rest/praise) stay functional and unfiltered.
- Rotation and no-repeat: 45s per pick by default (`RSI_STATUSLINE_QUIP_SECONDS` to tune, e.g. 5), stable within a bucket so nothing flickers; `~/.rsi/state/statusline_quips.json` tracks the last 300 shown per session and avoids repeats.
- Situational pools: morning (06–11h), late night (23–06h), context nearly full (≤15%), long-session rest reminders (≥45 min, tunable via `RSI_STATUSLINE_REST_MINUTES`, 0 disables), and RSI high-adoption praise.
- The RSI segment walks up from cwd to `.rsi/logs/events.jsonl` for today's stats, and is silently omitted when absent.
- Exactly one language is rendered, resolved via: `--lang` → `RSI_LANG` env → project `.rsi/config.yaml` → user `~/.rsi/config.yaml` → system locale. Write `lang: zh` once to pin it (project level wins).
- Flags: `--lang zh|en`, `--no-rsi`, `--no-quip`, `--plain` (no colors or emoji); `NO_COLOR` disables colors only.
- Cold start is ~250ms, within the host's ≥300ms refresh cadence; the command always exits 0 and falls back to a minimal line on error.

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
| `rsi_explore` | Drill the knowledge tree by domain module (empty path lists top modules with counts; a path lists member items title/brief/id plus graph-based related / conflicts edges for that node) |
| `rsi_feedback` | Adoption feedback; rejected+comment is distilled into a prohibition |
| `rsi_learn` | Reading packs (pack_list / pack_open / pack_done), teach_catch / teach_record, session audit |
| `rsi_knowledge_add` / `rsi_knowledge_search` / `rsi_knowledge_delete` / `rsi_knowledge_update` / `rsi_knowledge_review` | Memory management and review; `rsi_knowledge_update` edits an entry in place (prefer updating over re-adding duplicates) and `merge` combines near-duplicates (keeps the more complete one, archives the rest, records a supersedes edge); distilled entries must carry a `pack_id` |
| `rsi_conflicts` | Query and adjudicate conflicts between short knowledge items |
| `rsi_memory_health` | Flag incorrect or stale memories: contradicting comment polarity opens `incorrect`, a changed source that lost the title phrase opens `stale_source`; only `resolve` archives — scan never writes the official store |
| `rsi_memory` | Memory index / open / rebuild / relationship graph |
| `rsi_review` | Harness improvement proposals and config snapshots |
| `rsi_stats` | Memory usage statistics |
| `rsi_impact` | Value/impact panel for evangelism: injected cost (chars + est. tokens), recall compaction, adoption/compliance, a time curve, and an A/B token-savings calculator (savings are only produced from a user-supplied host before/after comparison — never invented) |

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
