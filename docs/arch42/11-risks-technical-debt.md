# Section 11: Risks and Technical Debt

## Risks

### R1: Legacy v1 Corpus Still on Disk

**Description**: v1 gold entries, silver files, and bronze copies may still exist in
`~/src/hippo-public/` or sibling paths. These are not part of the v2 pipeline but could
cause confusion if a future contributor assumes they are current.

**Impact**: Developer confusion, accidental use of stale data.

**Mitigation**: The README.md at `docs/arch42/README.md` clearly marks v1 as historical.
The v1 scripts (`ingest.py`, `compact.py`, `extract.py`, `reconcile.py`) remain in
`scripts/` for reference but are not wired to any schedule.

**Planned action**: Manual cleanup per `~/.hippo/STATUS.md` "Manual cleanup" section.
Remove after v2 has produced verified useful traces (i.e., after milestone 7 completes).

### R2: Observer Sessions Accumulated Pre-Cutoff Noise

**Description**: Before the `SessionStart` hook was registered (pre 2026-04-28), the
`claude-mem` plugin and other observers accumulated sessions in `~/.claude/projects/` that
were ingested by the v1 pipeline. These may have produced gold entries or silver files
with noise.

**Impact**: If any v1 gold entries are still in `gold/entries/` and somehow reach an agent,
they may carry inaccurate or stale knowledge.

**Mitigation**: The cutoff (ADR-007) prevents these sessions from being ingested by v2.
The gold entries directory is gitignored for private data; sample entries in
`gold/sample-entries/` are schema references only.

### R3: No Consolidation Yet Implemented

**Description**: The trace store will grow indefinitely until consolidation (milestone 10)
is implemented. Without decay and pruning, old and irrelevant traces accumulate.

**Impact**: Recall quality degrades over time as the trace store fills with low-signal
entries. Recall injection token budget may be exhausted by old traces before relevant
recent traces are surfaced.

**Mitigation**: The trace store is per-project JSONL. A manual prune (`jq` filter on
`strength`) can be run if the store becomes unwieldy before milestone 10 lands.

**Timeline**: Queued as milestone 10, after recall injection (milestone 9) is complete.

### R4: Recall Feedback Signal Undefined

**Description**: The consolidation pass needs a signal to know which traces were useful
(strengthen) vs unused (decay). This signal is not yet defined or instrumented.

**Impact**: Consolidation cannot distinguish good traces from bad traces without usage signal.
The default heuristic (first 3 agent turns mentioning trace content) is speculative.

**Mitigation**: This is a known open item per `~/.hippo/STATUS.md` "Decisions still open".
The feedback signal design is deferred until after recall injection is working and can be
observed empirically.

### R5: Multiple Concurrent Sessions Share thread_id

**Description**: If the user opens two Claude Code sessions in the same repo on the same
branch simultaneously, both sessions share a `thread_id`. The episode extractor must handle
interleaved turns.

**Impact**: Extracting traces from interleaved sessions may produce confused or duplicated
traces if the extractor processes each session independently without awareness of the other.

**Mitigation**: The episode extractor [QUEUED] should process sessions in `started_at` order
within a thread and include de-duplication logic. Full interleaving handling is a known
design gap for milestone 7.

---

## Technical Debt

| Item | Location | Description | Priority |
|------|----------|-------------|----------|
| v1 scripts still present | `scripts/ingest.py`, `compact.py`, `extract.py`, `reconcile.py` | Unused in v2 but on disk. Confusion risk for contributors. | Low (cleanup after milestone 7) |
| v1 launchd plists | `scripts/launchd/` | Old `com.mu.hippo.nightly` and `com.mu.hippo.retry` plists. The nightly was unloaded; the retry plist status is unclear. | Low |
| No schema validation on traces | Trace store | Until the extractor validates trace shape on write, malformed traces can accumulate. | Medium (milestone 7) |
| No integration test for sidecar gate | `scripts/ingest_v2.py` | The sidecar gate is the primary noise filter; it has no automated test. | Medium |
| Quota-graceful stop not implemented | `scripts/extract_episodes.py` | Risk of corrupted partial run if quota is exhausted mid-extraction. | High (milestone 11, before production use) |
