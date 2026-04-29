# Section 9: Architectural Decisions

## ADR Index

| ID | Title | Status | Date |
|----|-------|--------|------|
| [ADR-001](decisions/adr-001-skills-based-architecture.md) | Skills-Based Architecture Over Custom MCP/CLI | accepted | 2026-04-15 |
| [ADR-002](decisions/adr-002-qmd-retrieval.md) | QMD as the Retrieval Layer | superseded | 2026-04-15 |
| [ADR-003](decisions/adr-003-split-pipeline.md) | Split Pipeline Between Desktop Tasks and Cloud Routines | accepted (v1) | 2026-04-15 |
| [ADR-004](decisions/adr-004-trace-not-session.md) | Memory Unit is the Trace, Not the Session | accepted | 2026-04-28 |
| [ADR-005](decisions/adr-005-sidecar-metadata.md) | Sidecar Metadata Over JSONL Mutation | accepted | 2026-04-28 |
| [ADR-006](decisions/adr-006-thread-identity.md) | Indefinite Thread Gap, Same Name = Same Thread | accepted | 2026-04-28 |
| [ADR-007](decisions/adr-007-fresh-start-cutoff.md) | Fresh-Start Cutoff Over Backfill | accepted | 2026-04-28 |

## Notes on Superseded ADRs

**ADR-002 (QMD)**: QMD retrieval is superseded by context injection. The QMD infrastructure
(and gold entries) from v1 remain on disk but are not part of the v2 pipeline. QMD may be
reconsidered for the vector index component of the trace store if a suitable local hybrid
search is needed.

**ADR-003 (split pipeline)**: The cloud Routine component (compact + extract + reconcile) does
not exist in v2. All pipeline steps run locally under launchd. The principle of splitting
local-filesystem work from repo-only work remains valid and will apply when a consolidation
cloud step is designed.
