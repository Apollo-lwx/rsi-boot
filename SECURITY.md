# Security & Permissions Statement — rsi-boot

This document answers the Agensi 8-point security review directly. Read it alongside
[`SKILL.md`](./SKILL.md).

## What this skill is

rsi-boot is a **local, zero-API-key Harness-RSI** for AI coding hosts (Cursor, Qoder,
Codex, Claude Code — "host" here always means the coding agent, never a network
endpoint). Memory and harness slots live on your machine under the current repo's
`.rsi/` so they follow the project across hosts, and are recalled/distilled through
a set of `rsi_*` MCP tools.

## Permissions requested: none

The main path runs entirely on the user's machine and requests **no permissions**:

| Capability | Requested | Notes |
| --- | --- | --- |
| Outbound network access | **No on the main path** | The default path makes no external calls. Optional enhance-layer model calls happen only after the user turns that layer on and writes an `api_key` value in `~/.rsi/config.yaml`. |
| Command / process execution | **Fixed argv only** | Read-only `git` (allowlisted subcommands) and, for `rsi wipe`, a fixed process listing. `shell` is never enabled and arguments are not interpolated into a shell string. |
| Browser control | **No** | No browser is opened or automated. |
| Environment-variable reads | **Own settings, plus an explicit reference** | Reads `RSI_*` settings (e.g. `RSI_HOME`, `RSI_PROJECT_ROOT`). A provider key is read only when the user config contains `api_key: env:VAR_NAME` or `${VAR_NAME}`. Well-known provider key variables are not read on their own. |
| Filesystem writes | **Scoped** | Only inside the repo's own `.rsi/` directory. |
| API keys / secrets | **Not on the main path** | Chunks that match secret rules are dropped. `--allow-sensitive` keeps a redacted copy only. Those secret rules cannot be disabled. |
| Conversation context | **Off unless `--consent`** | Changelog-style decision notes and loose IDE markdown are listed only with `--consent`. `agent-transcripts` directories and IDE `.jsonl` / database chat logs are never opened. |

The **only** executable involved is the skill's own local MCP server, run as
`rsi serve` (installed via `pip install rsi-boot`). Launching its own server is
inherent to any local MCP skill and grants no additional external access.

## Network hosts declared: none

There are **no network hosts to declare or contact**. The published bundle contains
only these files:

- `SKILL.md` — the skill definition (this listing's entry point)
- `SECURITY.md` — this statement
- `plugin.json` — manifest; carries no homepage, repository, or network URL
- `mcp.json` — declares only how to launch the local server, no endpoints
- `README.md`, `README.en.md` — user documentation
- `LICENSE`
- `assets/logo.png` — PNG icon (no embedded namespace URL)

No file requests or references any external host or endpoint.

## Fail-closed gates

rsi-boot has three read-side safety gates, and their failure paths follow one rule
set: **a gate that cannot decide must not let content through silently.**

| Gate | What it does | Failure behavior |
| --- | --- | --- |
| Prompt masking (`rsi_boot.core.masking`) | Redacts secret-looking chunks before they are injected into prompts or proposals. | A single broken user rule is skipped and logged as an error; built-in locked secret rules cannot be disabled. At security decision points (`strict=True`, e.g. the pre-proposal scan) an incomplete masking pass raises instead of returning partially masked text. |
| Injection blocklist (`rsi_boot.injector.blocklist`) | Rejects recalled/injected text that matches known prompt-injection patterns. | If the blocklist file itself fails to load, **every** candidate is treated as blocked (deny-all) and an error is logged; a single malformed pattern is skipped and logged. |
| Proposal approval (`rsi_boot.learning.static_gate`) | Runs the masking scan before a distilled proposal can enter review. | Masking failure aborts the proposal: the gate raises and the proposal engine records a rejection — a proposal whose text cannot be fully scanned is never queued for review. |

General conventions:

1. **Checker failure = deny.** If a gate's own machinery fails at a decision point,
   the answer is "no" plus an error log, never a silent pass.
2. **Single-rule failure = skip that rule.** A malformed user-supplied rule is
   dropped with an error log and does not crash startup or block other rules.
3. **Strict at decision points.** Masking used for a safety verdict runs with
   `strict=True` so partial output cannot be mistaken for clean output.

## About the `evil.sh` reference in a prior screening

A previous automated screening referenced a "declared host" using a placeholder
throwaway domain (`evil.sh`). To be clear:

- That hostname is **not declared, contacted, or referenced as an endpoint** anywhere
  in this published bundle (it appears in no submitted file except this explanatory
  note).
- In the source repository, that string occurs only inside an **automated defensive
  test** under `tests/` which asserts that the skill's injection guard **rejects**
  piping a remote script into a shell. It is a negative test — proof the capability is
  blocked, not requested.
- The `tests/` directory is **intentionally excluded** from this submission: rsi-boot
  ships as a pip-installable package, so the listing carries only the skill manifest
  and documentation. The published artifact therefore performs no such action and
  declares no such host.

If a re-review still cites it, it is echoing a stale finding from an earlier, larger
archive. Please re-run the AI screening against the current files listed above.
