---
id: adr-001
status: accepted
date: 2026-04-15
---

# ADR-001: Skills-Based Architecture Over Custom MCP/CLI

## Context

Hippo needs a way for agents to query memory and for the pipeline to process sessions. The initial design proposed a custom MCP server and CLI binary. This would require building, testing, packaging, and maintaining two separate interfaces.

## Decision

Use Claude Code skills and Python scripts instead of a custom MCP server or CLI. The entire system is a claude-team project that Claude Code operates in directly.

Two levels of skills:
1. **hippo-remember** (portable): Installed in any project. Tells the agent how to query memory via `claude -p` pointed at the Hippo project.
2. **Pipeline skills** (project-local): Playbooks inside the Hippo project for ingest, compact, reconcile, status.

## Rationale

- No MCP server to build, deploy, test, or maintain.
- No CLI binary to package and distribute.
- Claude Code is both the runtime and development environment.
- The same skill works for humans (typing in terminal) and agents (subagent delegation).
- Adding features means adding a playbook and a script, not modifying a server.
- Testing is organic: use the system, fix what breaks.

## Consequences

- Depends on Claude Code being available (not usable without it).
- Each memory query costs a `claude -p` call against Max subscription.
- No standalone tool that could be used outside the Claude ecosystem.
- Accepted: these tradeoffs are fine for a single-user system where Claude Code is the primary interface.
