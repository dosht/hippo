---
id: adr-004
status: accepted
date: 2026-04-28
---

# ADR-004: Memory Unit is the Trace, Not the Session

## Context

v1 used the session as the primary memory unit: a full session transcript was compacted into
a silver summary, then extracted into gold markdown documents. Agents queried the gold layer.

Problems with session-level memory:
- Sessions are too long to inject directly. Compaction introduces information loss.
- Gold documents aggregate across sessions, destroying the causal `(action, feedback, adjustment)`
  unit of learning (confirmed by MemRL research).
- The agent had to ask for memory explicitly. It could only recall what it already suspected
  was relevant.

## Decision

The atomic memory unit is the **episodic trace**: a single `(type, cues, body)` triple extracted
from one session. Traces are stored in `~/.hippo/traces/<project_id>.jsonl`. Recall works by
injecting a small bundle of top-K traces into the context window automatically.

## Rationale

- A trace is the right granularity: specific enough to be concrete, small enough to inject.
- The `(action, feedback, adjustment)` structure is preserved inside the trace body.
- Multiple traces can be selected from different sessions, different projects, different threads,
  giving broader coverage than a session-level recall.
- Injection is automatic: the agent does not need to ask.
- Consolidation (strengthen/decay/prune) operates naturally on individual traces.

## Consequences

- Episode extractor must produce terse, standalone traces. No cross-references between traces.
- The trace store per project can grow unboundedly without consolidation. Consolidation
  (milestone 10) is required for long-term health.
- v1 gold entries are not compatible. They remain on disk as historical artefacts.
