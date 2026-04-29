---
id: MVP-2
title: "Hippo MVP-2: Quota Resilience and Cost Optimization"
status: ready
priority: high
---

# MVP-2: Quota Resilience and Cost Optimization

## Goal

Make the v1 bronze/silver/gold pipeline actually work under real-world quota pressure and real
session noise. MVP-1 proved the architecture is sound; MVP-2 makes it reliable. Six surgical bug
fixes land in MVP-2-01. Two token-cost optimizations land in MVP-2-02. Three further efficiency
gains land in MVP-2-03. No new data formats, no replacement of QMD, no replacement of the
hippo-remember skill or the reconcile cluster+judge pattern.

The v2 redesign attempted on 2026-04-28 was reviewed and rejected. All stories in this epic work
within the existing architecture.

## Out of Scope / Non-Goals (MVP-2)

- Any changes to gold frontmatter schema
- Any replacement of QMD as the retrieval layer
- Any replacement of the hippo-remember skill
- Any replacement of the reconcile cluster+judge pattern
- Tool-results sidecar ingestion (remains deferred)
- Agent-scoped filtering
- Multi-harness ingestion (Hermes, Codex adapters)
- Auto-promotion to CLAUDE.md

## Acceptance Criteria

- [ ] A nightly run under quota pressure produces partial gold candidates (sessions that completed before the quota wall yield gold; sessions that did not are left in silver, not marked failed)
- [ ] `claude-mem` and other plugin-spawned sessions are never ingested as real sessions (no sidecar entry, no admission to the pipeline)
- [ ] Multi-day work on the same project/branch consolidates into a single gold entry per thread rather than producing one fragmented entry per session
- [ ] Pre-quota-wall sessions from before 2026-04-28 remain invisible by default (HIPPO_INGEST_FROM cutoff in effect)
- [ ] The 1,588 quota-mismarked manifest rows confirmed reset to bronze status (manifest.jsonl.bak.20260428-143535 is the backup reference)
- [ ] Recurring nightly run cost on a stable corpus is measurably lower than the MVP-1 baseline (prompt-prefix cache hits reduce billed tokens; model routing sends small sessions to Haiku)
- [ ] No regression in gold quality scores as judged by the reconcile judge step

## Stories

| ID | Title | Status | Effort | Depends |
|----|-------|--------|--------|---------|
| [MVP-2-01](MVP-2-01.md) | MVP-1 Bug Fixes (quota recovery, sidecar gate, project identity, history flood, thread consolidation) | ready | L | - |
| [MVP-2-02](MVP-2-02.md) | Optimization Phase 1 (per-session pipeline loop, prompt caching) | ready | M | MVP-2-01 |
| [MVP-2-03](MVP-2-03.md) | Optimization Phase 2 (model routing, incremental reconcile, eligibility filter decision) | ready | M | MVP-2-02 |

## Technical Notes

### What Did Not Change

The bronze/silver/gold medallion structure, the QMD retrieval layer, the hippo-remember skill, and
the reconcile cluster+judge pattern are all unchanged by this epic. MVP-2 touches only:
`scripts/ingest.py`, `scripts/compact.py`, `scripts/extract.py`, `scripts/reconcile.py`, and a new
file `scripts/hooks/session_start.py`. The manifest schema gains three new fields
(`thread_id`, `project_id`, `sidecar_path`) but remains append-compatible.

### Research Grounding

The decision to keep near-miss and tool-only sessions in the pipeline (not filter them out) is
grounded in MemRL (Jan 2026): near-miss failures are more valuable than clean successes. Eligibility
filtering by session type is explicitly rejected in MVP-2-03.

The requirement for concrete values in gold entries (port numbers, exact file paths, exact error
messages) is grounded in "Not Always Faithful" (Jan 2026): agents follow raw concrete experiences,
not abstract summaries.

The thread_id consolidation approach (sha1 of project_id + branch) means reused branch names
collapse to the same thread. This is an accepted trade-off per design: reused branches are rare in
practice and the consolidation benefit outweighs the edge case.

### Manifest Backup Reference

`manifest.jsonl.bak.20260428-143535` is the authoritative backup created before the 2026-04-28
quota-reset operation. MVP-2-01 verifies the reset was applied correctly before any new pipeline
runs.

## Definition of Done

The epic is done when:

1. A nightly run on a corpus with more than 50 sessions completes with at least partial gold output
   despite hitting the quota wall mid-run.
2. `grep "claude-mem" manifest.jsonl` returns no rows with status silver or gold.
3. `jq -r .thread_id manifest.jsonl | sort | uniq -c | sort -rn` shows multi-day sessions
   consolidated under shared thread IDs.
4. All epic-level acceptance criteria above are checked.
5. `manifest.jsonl` billed-token count per run (logged by compact.py and extract.py) is lower than
   the MVP-1 baseline run on the same corpus.

## Test Note (applies to all MVP-2 stories)

End-to-end validation MUST be performed via the `hippo-remember` skill, invoked through `claude -p`,
not by querying QMD directly and not by reading gold entries from disk. The tester asks a natural
language question relevant to the recently-ingested sessions and verifies that the agent's response
contains the expected answer (e.g., a specific port, fix, or near-miss noted in a gold entry).

The reason: hippo-remember is the contract. Direct QMD or filesystem checks pass even when the
retrieval surface is broken. The skill exercises the full chain (subagent context, QMD search,
LLM rerank, answer synthesis), which is what real agents will hit. Unit-level fixture tests
(specified per-story) remain valuable for fast feedback, but the acceptance gate for each story
is a `claude -p` invocation against `hippo-remember`.

Example shape:

```bash
claude -p --model claude-sonnet-4-5 \
  "Use the hippo-remember skill to answer: <question targeting an entry produced by this story>"
```

The tester records the prompt, the agent's reply, and a pass/fail verdict in the story's test log.

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| SessionStart hook not fired in all harness versions | Medium | Log missing sidecar at ingest time; operator can manually backfill sidecar for known-good sessions |
| Haiku silver quality too lossy on small sessions | Medium | Escalate-to-Sonnet-on-retry path in MVP-2-03; quality gate in reconcile judge catches regressions |
| thread_id collision across unrelated reused branch names | Low | Accepted trade-off per design; clusters will be re-judged and non-cohesive entries will be rejected by the judge |
| Prompt cache miss rate higher than expected (e.g., prompt changes between runs) | Low | Cache is best-effort; falls back to uncached billing without breaking correctness |
| Incremental reconcile misses a cluster that changed | Low | Full reconcile remains available as a manual override; weekly scheduled full run as safety net |
