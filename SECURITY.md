# Security & Permissions Statement — rsi-boot

This document answers the Agensi 8-point security review directly. Read it alongside
[`SKILL.md`](./SKILL.md).

## What this skill is

rsi-boot is a **local, zero-API-key memory layer** for AI coding hosts (Cursor, Qoder,
etc. — "host" here always means the coding agent, never a network endpoint). It stores
project conventions, prohibitions and repair lessons on your machine under the repo's
`.rsi/` directory, and recalls/distills them through a set of `rsi_*` MCP tools.

## Permissions requested: none

The main path runs entirely on the user's machine and requests **no permissions**:

| Capability | Requested | Notes |
| --- | --- | --- |
| Outbound network access | **No** | Runs offline; the main path makes no external calls and needs no API key. |
| Command / process execution | **No** | The skill never invokes a system shell or spawns processes. |
| Browser control | **No** | No browser is opened or automated. |
| Environment-variable reads | **Own config only** | Reads only its own `RSI_*` settings (e.g. `RSI_HOME`, `RSI_PROJECT_ROOT`); it does not harvest the user's environment for secrets. |
| Filesystem writes | **Scoped** | Only inside the repo's own `.rsi/` directory. |
| API keys / secrets | **No** | Zero API keys on the main path; nothing sensitive is collected. |

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
