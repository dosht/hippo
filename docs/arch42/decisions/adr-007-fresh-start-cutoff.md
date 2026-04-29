---
id: adr-007
status: accepted
date: 2026-04-28
---

# ADR-007: Fresh-Start Cutoff Over Backfill

## Context

When Hippo v2 is set up, there are potentially years of existing session JSONL files in
`~/.claude/projects/`. These sessions have no sidecar records (the SessionStart hook did
not exist), so they would all be skipped by the sidecar gate.

Options:
1. **Backfill**: reconstruct identity metadata from session content (infer git context from
   cwd embedded in early messages, etc.) and ingest old sessions.
2. **Fresh start**: set a cutoff date (`HIPPO_INGEST_FROM`). Sessions before the cutoff are
   permanently skipped. Memory starts from the cutoff forward.

## Decision

Fresh start. Set `HIPPO_INGEST_FROM=2026-04-28` in `~/.hippo/config`. Pre-cutoff sessions
are skipped forever, regardless of whether a sidecar can be reconstructed.

## Rationale

- Pre-hook sessions include an unknown quantity of claude-mem plugin sessions, observer
  sessions, and other noise. There is no reliable way to distinguish them from real user
  sessions without the sidecar gate.
- Backfill reconstruction is fragile: session content does not reliably contain git context,
  and inferring project identity from working directory paths embedded in messages is
  error-prone.
- The cost of skipping old sessions is low: those sessions were never part of any memory
  system before. Starting fresh does not remove anything the agent was relying on.
- Memory quality is more important than memory quantity. A clean corpus from the cutoff
  forward is more valuable than a large noisy corpus.
- The cutoff mechanism is also useful as a future tool: if the trace store becomes polluted,
  advance the cutoff and start fresh again.

## Consequences

- All sessions before 2026-04-28 are permanently skipped. This is irreversible.
- The cutoff must never be moved backward. Moving it backward re-admits pre-cutoff sessions
  that may include observer noise.
- The cutoff date is the baseline: memory quality degrades gracefully toward the baseline
  (older sessions have more decayed traces), not toward zero.
