# RSI Boot

## Tagline
Repo-bound memory and a self-improving harness for AI coding agents — zero API keys.

## Description
RSI Boot is a local MCP server that gives AI coding agents persistent, repo-bound memory. Conventions, prohibitions, fix lessons and harness tunables all live in the project's own `.rsi/` directory, so memory moves with the repository — across machines and across hosts (Cursor, Claude Code, Codex, Qoder) with nothing to relearn.

Unlike plain memory plugins, RSI Boot also improves the harness itself: negative feedback goes into failure buckets, improvement proposals must pass static gates and human approval, are observed for 7 days, and roll back automatically if acceptance drops. The main path calls no model and needs no API key; every write is gated by approval workflows, prohibition lists and conflict adjudication.

Built for developers and teams who use AI coding agents daily and want per-project memory that is portable, auditable, and gets better the more they use it.

## Setup Requirements
- No environment variables or API keys are required on the main path.
- `RSI_PROJECT_ROOT` (recommended): absolute path of the workspace whose memory this server should bind to. In a project-level `.cursor/mcp.json`, use `"${workspaceFolder}"`. Do not set it in a user-level config, where `${workspaceFolder}` may not expand.
- Optional: a model key (e.g. `OPENAI_API_KEY`) only if you enable LLM-enhanced extraction or vector search in `~/.rsi/config.yaml`. Everything works without it.
- Requires Python 3.10+. Install from PyPI with `pip install rsi-boot` (or run it directly with `uvx --from rsi-boot rsi serve`), then add the `rsi serve` MCP configuration to your project.

## Category
Developer Tools

## Use Cases
Persistent per-project memory for AI coding agents, Recalling project conventions and prohibitions before tasks, Learning from user corrections and negative feedback, Porting memory across Cursor, Claude Code, Codex and Qoder, Sharing conventions with the team through the repo, Self-improving harness with gated proposals and rollback, Local-first knowledge base with no API keys

## Features
- Repo-bound `.rsi/` storage: memory follows the project across machines and hosts
- Zero API keys on the main path — no model calls, fully local
- Harness-RSI loop: proposals → static gates → human approval → 7-day observation → auto-rollback
- Approval-gated writes with prohibition lists and conflict adjudication
- 15 MCP tools covering recall, explore, learn, feedback, review, stats and impact
- Knowledge tree organized by domain lanes with drill-down browsing
- Conflict detection and resolution for duplicate or contradicting knowledge
- Memory health scanning for incorrect or stale entries
- Acceptance-rate tracking; rejections are distilled into new prohibitions
- Genome bundle export for backup and cross-project migration
- One MCP server works with Cursor, Claude Code, Codex and Qoder
- Optional status line for Claude Code and Cursor CLI with RSI recall stats
- Optional vector search and LLM-enhanced extraction with your own keys

## Getting Started
- "Learn this project" — bootstrap distills repo knowledge into reviewable packs
- "What conventions should I follow in this repo?"
- "Remember: this project uses snake_case everywhere"
- Tool: rsi_recall — Recall prohibitions and conventions before starting a task
- Tool: rsi_learn — Distill project knowledge and record lessons
- Tool: rsi_feedback — Report whether recalled guidance was accepted or rejected
- Tool: rsi_knowledge_review — Approve distilled knowledge before it enters recall

## Tags
memory, agent-memory, mcp, self-improvement, harness, knowledge-base, repo-bound, cursor, claude-code, codex, qoder, conventions, feedback-loop, rag, local-first, no-api-key, developer-tools, productivity, python

## Documentation URL
https://github.com/Apollo-lwx/rsi-boot#readme

## Health Check URL
N/A — local stdio server; health checks apply to hosted servers only.
