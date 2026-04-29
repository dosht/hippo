# Hippo - Architecture (arc42)

> **Version**: v2 (target architecture). v1 (bronze/silver/gold medallion + QMD retrieval) is historical context only.
> **Status**: Sections 1-8 reflect v2 design. Components marked [QUEUED] are designed but not yet implemented.

## Sections

| Section | File | Topic |
|---------|------|-------|
| 1 | [01-introduction-goals.md](01-introduction-goals.md) | Purpose, quality goals, stakeholders |
| 2 | [02-constraints.md](02-constraints.md) | Technical and organisational constraints |
| 3 | [03-context-scope.md](03-context-scope.md) | System boundary, external interfaces |
| 4 | [04-solution-strategy.md](04-solution-strategy.md) | Agent-hippocampus framing, key decisions |
| 5 | [05-building-block-view.md](05-building-block-view.md) | Capture, consolidation, recall layers |
| 6 | [06-runtime-view.md](06-runtime-view.md) | Session start, nightly run, recall injection sequences |
| 7 | [07-deployment-view.md](07-deployment-view.md) | launchd, ~/.hippo data root, Claude Code host |
| 8 | [08-crosscutting-concepts.md](08-crosscutting-concepts.md) | Trace model, identity rules, quota patterns |
| 9 | [09-architectural-decisions.md](09-architectural-decisions.md) | ADR index |
| 10 | [10-quality-requirements.md](10-quality-requirements.md) | Durability, noise isolation, quota-graceful |
| 11 | [11-risks-technical-debt.md](11-risks-technical-debt.md) | Known risks, legacy corpus, open items |
| 12 | [12-glossary.md](12-glossary.md) | Term definitions |

## ADRs

| ID | Title | Status |
|----|-------|--------|
| [ADR-001](decisions/adr-001-skills-based-architecture.md) | Skills-Based Architecture Over Custom MCP/CLI | accepted |
| [ADR-002](decisions/adr-002-qmd-retrieval.md) | QMD as the Retrieval Layer | superseded (v1) |
| [ADR-003](decisions/adr-003-split-pipeline.md) | Split Pipeline Between Desktop Tasks and Cloud Routines | accepted (v1, partially applies to v2) |
| [ADR-004](decisions/adr-004-trace-not-session.md) | Memory Unit is the Trace, Not the Session | accepted |
| [ADR-005](decisions/adr-005-sidecar-metadata.md) | Sidecar Metadata Over JSONL Mutation | accepted |
| [ADR-006](decisions/adr-006-thread-identity.md) | Indefinite Thread Gap, Same Name = Same Thread | accepted |
| [ADR-007](decisions/adr-007-fresh-start-cutoff.md) | Fresh-Start Cutoff Over Backfill | accepted |

## v1 Historical Context

v1 used a bronze/silver/gold medallion pipeline producing queryable markdown gold entries retrieved
via QMD hybrid search and a memory subagent. The RL-framing and the bronze immutability principle
carry forward to v2. The gold-as-document layer, QMD retrieval, and silver compaction step are
replaced by episodic traces and context injection.

Legacy v1 scripts still on disk: `scripts/ingest.py`, `scripts/compact.py`, `scripts/extract.py`,
`scripts/reconcile.py`. Not executed by the v2 pipeline.
