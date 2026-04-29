---
id: adr-005
status: accepted
date: 2026-04-28
---

# ADR-005: Sidecar Metadata Over JSONL Mutation

## Context

Hippo needs to associate project identity, branch, and thread identity with each session.
This metadata is not present in the Claude Code session JSONL files themselves (they contain
conversation turns but not git context).

Two options:
1. **Mutate the JSONL**: append a metadata header to the session file at session start.
2. **Sidecar file**: write metadata to a separate append-only file (`~/.hippo/sessions.jsonl`).

## Decision

Use a sidecar file. The `SessionStart` hook appends one JSON record per session to
`~/.hippo/sessions.jsonl`. The session JSONL in `~/.claude/projects/` is never modified.

## Rationale

- Claude Code owns the session JSONL format. Mutating it risks breaking Claude Code's own
  readers or triggering unexpected behaviour.
- A sidecar is easy to inspect, backup, and recover independently.
- Append-only semantics are simple and robust. No lock needed; no partial-write corruption.
- The sidecar doubles as the trust filter: any session without a sidecar entry is
  definitionally not from the user's own sessions.
- Hook fires happen in a separate process; writing to the session JSONL while Claude Code
  is also writing to it would require file locking.

## Consequences

- The sidecar and the session JSONL are separate files that must be joined by `session_id`.
  Ingest reads both and joins them.
- If the hook fails to fire (e.g., hook not registered, script error), no sidecar record
  is written and the session is permanently skipped. This is acceptable: better to skip a
  session than to ingest untrusted data.
- Multi-row sidecar entries for the same session (hook fires twice on re-entry) are handled
  by last-write-wins in ingest.
