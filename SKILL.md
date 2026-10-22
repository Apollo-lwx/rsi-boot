---
name: rsi-boot
description: Repo-bound Harness-RSI for AI coding hosts — not just another agent memory. Improve the harness from failures (rsi_review), keep memory in the repo's .rsi/ so it follows the project across Cursor/Qoder/Codex/Claude Code, recall conventions and prohibitions before a task (rsi_recall/rsi_explore), and distill verified lessons after a fix (rsi_learn, rsi_knowledge_add). Main path needs no API key and makes no network calls. Use when you want harness self-improvement, project-portable memory, switching hosts without re-teaching, or mention RSI, recall, distill, teach, closeout. Triggers: memory, harness, conventions, prohibitions, lessons, recall, distill, inject rules, migrate project.
license: MIT
tags: [harness, memory, self-improvement, mcp, knowledge, local-first, portable, recall, distill]
metadata:
  version: "0.1.0"
  mcp-server: rsi-boot
---

# RSI Boot — Harness-RSI that Follows the Project

Plenty of tools already store agent memory. RSI Boot does two other jobs: **rsi-harness**
improves prompts, skills and recall strategy from failures; **memory lives in the
repo's `.rsi/`** so it travels with the project when you move machines or switch
Cursor / Qoder / Codex / Claude Code. It still recalls conventions and prohibitions
*before* a task and distills verified lessons *after* it.

**Privacy / security:** the main path is **fully local and offline**. It needs **no
API key**, opens **no connections**, and writes **only** inside the repo's own `.rsi/`
directory; the only thing it runs is its own local MCP server (`rsi serve`). There is
nothing external to declare or reach. See [`SECURITY.md`](./SECURITY.md) for the full
permissions statement and the explanation of the `evil.sh` test-only reference.

This skill tells you **when and how to drive the rsi-boot MCP tools**; it does not
reimplement them.

## Prerequisites

The `rsi` MCP server must be reachable. Confirm the `rsi_*` tools are listed; if
not, configure the server:

```json
// mcp.json
{
  "mcpServers": {
    "rsi-boot": {
      "command": "rsi",
      "args": ["serve"],
      "env": { "RSI_PROJECT_ROOT": "${workspaceFolder}" }
    }
  }
}
```

Requirements: Python 3.10+, `rsi` on PATH (`pip install rsi-boot`). User config at
`~/.rsi/config.yaml` (override with `RSI_HOME`); project data at `<repo>/.rsi/`,
isolated per project.

## Core workflow

### 1. Recall before working
At task start call `rsi_recall` (and `rsi_explore` for code/neighbors).
**Prohibitions surface first** — treat them as hard constraints. Use
`rsi_knowledge_search` / `rsi_query` for targeted lookups and `rsi_conflicts` for
contradictory memories.

### 2. Work under the recalled rules
Apply conventions while doing the actual task.

### 3. Distill after learning
When you fix a user-visible bug, are corrected on approach, or change a shared
rule, capture it via `rsi_learn` and/or `rsi_knowledge_add` (set `content_type`; a
lesson must include the wrong action and the correct fix). Don't skip small fixes —
those are the reusable cases.

### 4. Review before injecting
Distilled items start **pending**. Only `rsi_review` / `rsi_knowledge_review`
(approve) promote them into injected rules; reject archives them. **Never edit
business code before the audit is finished.** Record outcomes with `rsi_feedback`
(valid action is `accepted`; there is no `adopted`) and measure value with
`rsi_impact` / `rsi_stats`.

## Tool reference

| Tool | Use it to |
| --- | --- |
| `rsi_recall` | Pull prohibitions + conventions for the current task (call first) |
| `rsi_explore` | Explore code and adjacent knowledge/graph edges |
| `rsi_query` / `rsi_knowledge_search` | Targeted lookups |
| `rsi_memory` | Inspect memory state |
| `rsi_learn` | Distill a pack/lesson from the session |
| `rsi_knowledge_add` / `_update` / `_delete` | Manage knowledge items |
| `rsi_knowledge_review` / `rsi_review` | Approve (inject) or reject pending items |
| `rsi_conflicts` | Detect contradictory memories |
| `rsi_memory_health` | Scan / list / explain / resolve incorrect or stale official memories |
| `rsi_feedback` | Record acceptance of a recalled item (`accepted`) |
| `rsi_impact` / `rsi_stats` | Measure token savings and memory health |

## Discipline (must follow)

- **Recall → work → distill → review.** Never inject without review.
- **No business-code edits before the audit is finished.**
- After fixing a user-visible bug or being corrected: **record a lesson** with
  wrong_action + correct_fix.
- Final summary must copy the closeout paths or state why skipped.
- Keep everything local — no external model or API key on the main path.
