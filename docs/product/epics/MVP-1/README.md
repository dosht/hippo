---
id: MVP-1
title: "Hippo MVP: End-to-End Write-Path to Read-Path Loop"
status: ready
priority: high
---

# MVP-1: End-to-End Write-Path to Read-Path Loop

## Goal

Prove and deliver the full Hippo loop in a single workflow: raw Claude Code sessions flow through
bronze, silver, and gold layers, producing queryable knowledge entries that any agent in any
project can retrieve via the memory subagent and hippo-remember skill. The MVP is complete when a
real query from a different project returns a concrete, grounded answer sourced from a session that
was ingested automatically.

The write path (ingest, compact, extract) and read path (QMD, memory subagent, hippo-remember
skill) must both be working and validated end-to-end before we call this done.

## Out of Scope / Non-Goals (MVP)

The following are explicitly deferred to Post-MVP:

- **Full-history ingestion of all ~590 sessions** (moved from MVP-1-11 on 2026-04-18 for capacity reasons; MVP validates against one active project instead)
- **Tool-results sidecar ingestion** (the `tool-results/*.txt` overflow files next to each session — JSONL preview is sufficient for MVP; full sidecar ingestion deferred)
- Reconciliation (cross-session contradiction and confirmation detection)
- Staleness detection and flagging
- Promotion candidates and gold/suggestions/ workflow
- Cloud Routine scheduling (nightly reconciliation via Anthropic cloud)
- Agent-scoped filtering (developer vs. tester query views)
- Multi-harness ingestion (Hermes, Codex, OpenClaw adapters)
- Cross-machine real-time sync beyond git pull + qmd rebuild
- Auto-promotion to CLAUDE.md or skills based on query_count threshold
- Feedback processing loop (feedback.jsonl -> confidence updates)
- Persistent memory agent sessions (stateful query caching)
- Obsidian vault visualization
- PostgreSQL or Supabase backend for QMD
- Compaction-aware session splitting or multi-session merging
- LightRAG or GraphRAG integration
- ACP query interface for non-Claude agents
- Complete status dashboard (staleness, suggestions, promotion candidates)

## Acceptance Criteria

- [ ] At least 5 hand-crafted gold entries exist in gold/entries/ with valid frontmatter (all required fields present, no em dashes)
- [ ] `qmd collection add ~/src/hippo/gold/entries --name hippo --mask "**/*.md"` runs without error and reports entries indexed
- [ ] `qmd query "test question" --collection hippo --json -n 5` returns results against the hand-crafted entries
- [ ] `git/hooks/post-merge` rebuilds the QMD index automatically after `git pull`
- [ ] Memory subagent (.claude/agents/memory.md) returns a structured answer (Answer, Confidence, Last validated) for at least 3 test queries using the hand-crafted entries
- [ ] hippo-remember skill is symlinked to ~/.claude/skills/hippo-remember and activates in a non-Hippo project
- [ ] A query issued from the Transgate frontend project returns a grounded answer from a gold entry (read-path cross-project smoke test passes)
- [ ] `scripts/ingest.py` scans ~/.claude/projects/, copies new JSONL files to bronze/, and appends to manifest.jsonl without duplicating entries already in the manifest
- [ ] `scripts/compact.py` processes bronze sessions to silver/ using `claude -p --model claude-sonnet-4-5 --max-turns 1` and produces silver summaries that preserve near-miss failures and concrete configuration details
- [ ] `scripts/extract.py` processes silver sessions to gold/entries/ with valid frontmatter, runs duplicate detection via `qmd query` before writing, and updates manifest status to "gold"
- [ ] The pipeline is idempotent: running ingest.py, compact.py, and extract.py twice on the same input produces no duplicate bronze files and no duplicate gold entries
- [ ] **5-session validation gate**: the pipeline is run end-to-end on exactly 5 hand-selected past sessions and a human reviewer confirms that the resulting gold entries are accurate, concrete, and non-redundant before full-history ingestion proceeds
- [ ] Full-history ingestion runs to completion (all sessions in ~/.claude/projects/ processed or skipped with a manifest entry), with a final gold entry count reported
- [ ] After pipeline run, `git add gold/ manifest.jsonl && git commit && git push` completes and the gold entries are visible in the remote repo
- [ ] `cat manifest.jsonl | jq -s 'group_by(.status) | map({status: .[0].status, count: length})'` returns correct counts (bronze, silver, gold statuses all present)
- [ ] Developer-guide README reflects any changes to conventions, file paths, or scripts introduced during the MVP build

## Stories

| ID | Title | Status | Effort | Depends |
|----|-------|--------|--------|---------|
| [MVP-1-01](MVP-1-01.md) | Seed Gold Entries | done | S | - |
| [MVP-1-02](MVP-1-02.md) | QMD Collection Setup and Indexing | done | S | MVP-1-01 |
| [MVP-1-03](MVP-1-03.md) | Git Post-Merge Hook for QMD Reindex | done | XS | MVP-1-02 |
| [MVP-1-04](MVP-1-04.md) | Memory Subagent Refinement | done | S | MVP-1-01, MVP-1-02 |
| [MVP-1-05](MVP-1-05.md) | hippo-remember Skill Cross-Project Install | done | S | MVP-1-04 |
| [MVP-1-06](MVP-1-06.md) | Read-Path Smoke Test from Another Project | done | S | MVP-1-03, MVP-1-05 |
| [MVP-1-07](MVP-1-07.md) | Build ingest.py | done | M | - |
| [MVP-1-08](MVP-1-08.md) | Build compact.py | done | M | MVP-1-07 |
| [MVP-1-08.5](MVP-1-08.5.md) | Silver-Only Read-Path Validation | done | XS | MVP-1-08, MVP-1-04, MVP-1-02 |
| [MVP-1-09](MVP-1-09.md) | Build extract.py | done | M | MVP-1-08, MVP-1-02 |
| [MVP-1-10](MVP-1-10.md) | 5-Session Validation Gate | done | M | MVP-1-07, MVP-1-08, MVP-1-09 |
| [MVP-1-11](MVP-1-11.md) | Scoped Pipeline Validation Run (kenznote-payments) | done | S | MVP-1-10, MVP-1-14, MVP-1-15 |
| [MVP-1-12](MVP-1-12.md) | Minimal Pipeline Status Command | done | XS | MVP-1-11 |
| [MVP-1-13](MVP-1-13.md) | Silver Frontmatter and Metadata Propagation | done | S | MVP-1-08, MVP-1-09 |
| [MVP-1-14](MVP-1-14.md) | Session Metadata Enrichment (gitBranch, cwd, session_started_at) | done | S | MVP-1-07, MVP-1-13 |
| [MVP-1-15](MVP-1-15.md) | Subagent Session Ingestion + Bronze Self-Containment | done | M | MVP-1-07, MVP-1-14 |

## MVP-1 Closeout (2026-04-26)

The hackathon session on 2026-04-26 closed out all open MVP-1 stories. See [`hackathon-brief.md`](../../../../hackathon-brief.md) for the full milestone-and-spike log; summary of outcomes:

**Pipeline shape**
- **Ingest** (M1): added user-turn-aligned chunked ingest for sessions over 250 KB (`scripts/ingest.py`); raised hard skip ceiling from 500 KB to 10 MB; new manifest fields `part_index`, `total_parts`. Greedy splitter then replaced with byte-balanced uniform-target version (S1.4) for tighter part-size variance.
- **Compact** (M2): rewrote `scripts/prompts/compact.md` for trajectory shape — chronological `(action, response, adjustment)` events, near-misses inline, concrete values inline, no thematic sections. Added `scripts/prompts/compact_continue.md` for parts 2..N of chunked sessions (prior silver as read-only context, append-only). Multi-part orchestration in `scripts/compact.py`: shared silver path, predecessor-must-be-silver eligibility, `silver_offset_bytes` per part for re-compact recovery. Transient retry covers rate-limit AND silent rc!=0 with empty stderr (S2.3).
- **Extract** (M3/S3.1): dedupe by silver_path so chunked sessions extract once. Frontmatter ownership split — model emits the 6 semantic fields (`id`, `type`, `topics`, `projects`, `agents`, `staleness_policy`) plus title and body; Python overrides the deterministic 7 (`source_sessions`, `created`, `last_validated`, `last_queried`, `query_count`, `supersedes`, `confidence`). Same retry parity as compact.
- **Recalibrated** compact ratio bands for trajectory output (S3.2): `RATIO_WARN_LOW` 40% → 1%; `RATIO_WARN_HIGH` 90% → 30%.
- **QMD** (S3.3): `hippo` collection registered (7 entries from kenznote-payments, 10 chunks embedded). Sample queries score 93% on direct topic matches.

**Doc cleanup (S3.4)**: `docs/playbooks/` directory removed entirely. `compact.md` and `extract.md` had already moved to `scripts/prompts/` in S2.1; `ingest.md` (orphan) and `status.md` (status.sh self-documents) deleted; `reconcile.md` moved to [`docs/brainstorm/reconcile-design.md`](../../../brainstorm/reconcile-design.md) reflecting design-only post-MVP status. arch42 reference updated.

**Stories closed**: MVP-1-11, MVP-1-12, MVP-1-14, MVP-1-15 → `status: done`. Progress 16/16.

**Known limitation**: the live compact run on 2026-04-26 hit a Claude API token quota partway through. 15/46 manifest entries reached silver (3 logical sessions: `534c2ba9` 3/3, `64cf529a` 5/6, `550caba1` 7/16); 31 entries can be re-processed by resetting `failed→bronze` and re-running `python -m scripts.compact` when quota refreshes. Trajectory shape, continuation flow, ratio bands, and extract pipeline are all validated end-to-end on the silvers we have plus a synthetic RL fixture (`tests/fixtures/synthetic_rl_session.jsonl`).

The 2026-04-25 Session Wrap-Up below is preserved as historical context — every open question and architectural concern in it has been addressed by the 2026-04-26 work.

## Session Wrap-Up (2026-04-25)

This section captures the state of play at the end of the 2026-04-25 working session,
the open issues discovered during a scoped pipeline run on `kenznote-payments`, and the
architectural questions the next session needs to resolve before more code lands.

### What was implemented this session

- **MVP-1-13** (silver frontmatter propagation) — done, shipped previously, merged.
- **MVP-1-14** (session metadata enrichment: `cwd`, `git_branch`, `session_started_at`)
  — implemented and tested (34 new tests). Status `review`. All three fields flow
  ingest → manifest → silver frontmatter → extract prompt.
- **MVP-1-15** (subagent session ingestion + bronze self-containment refinement) —
  implemented in two passes:
  - First pass: subagent JSONL discovery, `agent`/`agent_task` lifted from `.meta.json`
    into the manifest, `parent_session` linkage, idempotent re-runs (11 + 3 tests).
  - Second pass (refinement, added 2026-04-25): bronze namespaced as
    `bronze/<harness>/` (`bronze/claude-code/` today), sibling `.meta.json` copied to
    bronze byte-identical, new manifest field `meta_path`, arc42 §"Bronze
    Self-Containment" subsection added, manifest migrated. Status `review` (5 more tests).
- **`--source` scoping fix** in `ingest.py` so a specific project dir works as input.
- **Clean-slate experiment**: bronze/silver/gold archived to `/tmp` by user, QMD
  collections wiped (`hippo`, `hippo-silver`), manifest reset, scoped re-ingest run on
  `kenznote-payments`. Manifest now has 15 entries (3 top-level + 9 subagents +
  3 skipped-large), bronze contains 19 files (10 jsonl + 9 meta.json sidecars).
- **Compact run** on the 9 eligible bronze sessions: 7 silvers written, 1 failed
  (`agent-a56f9ab108f2c12ca`, `rc=1`, empty stderr — root cause unclear), 2 not yet
  attempted (run was stopped by user at session 8/10 to review).

Tests: **188 pass**, baseline preserved.

### Open architectural questions discovered during this session

These are not yet captured as stories. They surfaced during review of the first compact
run and need to be discussed before further pipeline iteration.

#### 1. Silver output shape is wrong (over-processed, missing trajectory)

The current `compact.py` prompt asks for a thematic summary with sections (Key Decisions,
Problems and Solutions, Configuration Details, Near-Misses and Gotchas, Open Questions).
That is **gold-shape**, not silver-shape. It denormalizes the session into facts and
destroys causal/temporal order.

Per the brainstorm framing (`docs/brainstorm/session-summary.md`, `related-work.md`
citing **Not Always Faithful** Jan 2026 and **MemRL** Jan 2026), silver should be a
**denoised RL-style trajectory**: action → environment feedback → adjustment, in
chronological order, with the reward signal (tool errors, test failures, user
corrections) as the backbone. The user's framing: "describe how the session went and
remove noise and highlight feedbacks to actions; record what happened, not the final
result."

Today silver and gold are doing similar work; gold is starved on already-distilled
silver input. Two-distillation problem.

#### 2. "Playbooks" are misused as prompts

`docs/playbooks/compact.md` and `docs/playbooks/extract.md` are human-facing pipeline
docs whose entire markdown body is currently fed verbatim to `claude -p` as the
prompt. The "playbook" concept is a claude-team convention for `/playbook` slash
commands (deterministic, repeatable agent tasks), not a place to keep model prompts.
Bleeds through as narration in silver output ("I'll compact this session following the
playbook guidelines you provided").

Resolution direction (not yet implemented): move prompts to `scripts/prompts/compact.md`
and `scripts/prompts/extract.md`, keep them as the actual instruction body only,
delete `docs/playbooks/` for compact/extract (status.md and reconcile.md are also
mis-categorized — status is a stub, reconcile is design-only and belongs in
`docs/brainstorm/` or `docs/arch42/decisions/`).

Skills (`.claude/skills/`) were considered but don't fit — skills are
discovery-driven; we call `claude -p` with explicit instructions inline, no discovery.

#### 3. Large sessions are silently discarded — but they're the most valuable

`LARGE_SESSION_BYTES_THRESHOLD = 500_000` causes any session over 500 KB to be marked
`skipped-large` with no bronze file copied. In the `kenznote-payments` scope, 4 of 5
top-level sessions and 1 of 9 subagents fell in this bucket — the longest, most
substantive work is being thrown away.

Resolution direction (not yet implemented): chunk-and-compact for large bronze.
Open design questions, all unresolved at end of session:
- Split boundary: byte-aligned, user-turn-aligned, or token-estimated.
- Manifest model: parts as separate entries (`<uuid>_part_N`, mirrors subagent pattern)
  vs. single entry with `bronze_paths` list.
- Compact prompt awareness: `part K of N` metadata so each chunk summarizes its slice
  rather than the whole arc.
- Hard skip threshold for pathological cases (multi-MB sessions).

#### 4. Echo-failure mode on big bronze sessions

In the compact run, 2 of 7 silvers (the 2 largest bronze inputs at 403 KB and 435 KB)
came out at <1% ratio with no playbook structure — the model echoed the assistant's
final closing message verbatim instead of compacting. Likely cause: when a session
already ends with a polished summary message, asking for another thematic summary
makes echo the path of least resistance. A trajectory-shaped prompt should mitigate
this because the model can't echo a "trajectory" from a final message.

#### 5. Compaction quality thresholds are calibrated for the wrong target

`RATIO_WARN_LOW = 0.40` (40%) trips on every successful compaction (real-world good
ratios cluster at 3–6%). The thresholds will need recalibration once the silver shape
is corrected — and the meaning of "below threshold" changes (catch echo-failure at
~<1% rather than catch insufficient compression at <40%).

#### 6. Compact failure with empty stderr

`agent-a56f9ab108f2c12ca` (tech-lead, "Research LS flow and update BILLING-01-01",
232 KB bronze) failed with `claude -p` `rc=1` and no stderr message. Not a rate
limit (no 429 signature). Cause unknown. The retry loop in `call_claude_compact`
only retries on rate-limit errors; non-429 failures fall straight to `failed` status.
Worth catching as a generic transient retry too.

### What is NOT done

- **MVP-1-11** (Scoped Pipeline Validation Run on kenznote-payments) — not complete.
  Compact partially run (7/10 silvers, 1 failure, 2 not attempted). No `extract` run.
  No `qmd update && qmd embed`. No memory-subagent verification.
- **MVP-1-12** (Minimal Pipeline Status Command) — `scripts/status.sh` is implemented
  but `docs/playbooks/status.md` still has P3 sections (staleness/promotion) that ACs
  require removed. Status `in-progress`.
- **Sign-off** on MVP-1-14 and MVP-1-15 still pending (both `review`).
- The four open architectural questions above (silver shape, playbook misuse,
  large-session chunking, recalibrated thresholds) are not yet captured as stories.

### Recommended next-session opening moves

1. Read this section, decide which of the four open architectural questions block
   MVP-1-11 completion and which can be deferred.
2. Rewrite the compact prompt for trajectory output BEFORE finishing MVP-1-11. Echo
   failure and over-processing will recur otherwise.
3. Move prompts out of `docs/playbooks/`; rename / relocate / delete those files.
4. Decide chunked-ingest design (or explicitly defer). If deferred, document the
   `skipped-large` data loss risk in the epic out-of-scope list.
5. Rerun compact + run extract end-to-end on the kenznote-payments scope. Sign off
   MVP-1-14, MVP-1-15, MVP-1-11, MVP-1-12 as appropriate.

### Files of interest for next session

- `/Users/mu/src/hippo/scripts/compact.py` — current prompt loader, the 40% threshold
  constant
- `/Users/mu/src/hippo/scripts/ingest.py` — `LARGE_SESSION_BYTES_THRESHOLD`, harness
  namespacing, subagent + meta.json copy logic
- `/Users/mu/src/hippo/docs/playbooks/compact.md` — the prompt-misused-as-playbook
- `/Users/mu/src/hippo/docs/brainstorm/session-summary.md` — original RL framing
- `/Users/mu/src/hippo/silver/2026-04-25_*.md` — the 7 silvers from this session;
  agent-af0491e7dd19ecae4 is a good example of detail preservation (despite wrong
  shape); agent-a79e58b151a81d0a2 and a0b4f1fa-… are the two echo failures
- `/Users/mu/src/hippo/manifest.jsonl.bak-experiment-2026-04-25` — pre-experiment
  manifest backup (1156 lines)

## Technical Notes

### Model Choice

All `claude -p` pipeline calls (compact.py, extract.py) use `--model claude-sonnet-4-5 --max-turns 1`.
Haiku is used only for the memory subagent (interactive, latency-sensitive). Sonnet is used for
offline pipeline processing (cost-tolerant, higher quality at nuance preservation).

Use `--no-mcp` on pipeline calls to prevent the script from loading MCP servers, which would add
latency and potential side effects.

### Idempotency Contract

All three scripts (ingest.py, compact.py, extract.py) must be safe to run multiple times on the
same input. The manifest is the source of truth for what has been processed. Each script checks
manifest status before doing any work. Bronze files and silver files are named by session ID so
file-level collisions are detectable.

### Required Frontmatter Fields

Every gold entry must contain all of: id, type, topics, projects, agents, confidence,
source_sessions, created, last_validated, last_queried, query_count, staleness_policy, supersedes.
See gold/sample-gold-entry-format.md for the canonical template. extract.py must validate
frontmatter completeness before writing; any entry with missing fields should be skipped and logged.

### 5-Session Validation Gate

MVP-1-10 is a hard gate before MVP-1-11. The developer must manually review silver summaries and gold entries
from the 5 validation sessions. Check that near-miss failures survived compaction (the MemRL
finding: near-misses are more valuable than successes). If they were lost, the compaction prompt
in compact.py needs adjustment before proceeding to full history.

### Concrete Detail Requirement

Gold entries must include specific values: port numbers, exact file paths, exact command flags,
exact error messages. Abstract entries like "configure the tool correctly" are not gold-worthy.
This is grounded in the "Not Always Faithful" (Jan 2026) finding that agents follow raw concrete
experiences, not abstract summaries.

### QMD Index is Derived

Never commit the QMD SQLite index. It is rebuilt via `qmd update && qmd embed`. The git
post-merge hook (MVP-1-03) automates this on pull. The extract.py script runs it at the end of each
extraction run (MVP-1-09). Both paths must be working for multi-machine sync to function.

### Session Size Limit

Sessions exceeding approximately 80K tokens (estimated as character count / 4) should be skipped
with status "skipped-large" rather than passed to claude -p, which may fail or produce low-quality
output. Large session handling is deferred to Post-MVP.

## Definition of Done

The epic is done when:

1. A developer opens a Claude session in a non-Hippo project (e.g., Transgate frontend).
2. They invoke the hippo-remember skill with a real operational question.
3. The memory subagent returns a grounded answer (Answer + Confidence + Last validated) sourced
   from a gold entry that was produced by the pipeline from a real past session, not a hand-crafted seed.
4. All 16 acceptance criteria above are checked.
5. gold/entries/ is committed and pushed to git with at least 15 total entries (seeds + auto-extracted).
6. manifest.jsonl shows at least 5 sessions with status "gold".
7. developer-guide README is current.

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Compaction loses near-miss failures | Medium | MVP-1-10 validation gate catches this at 5 sessions; iterate on prompt before full history |
| extract.py produces too-abstract entries | Medium | Concrete detail requirement enforced in extraction prompt; human review in MVP-1-10 |
| claude -p API errors during bulk pipeline run | Low | compact.py and extract.py log failures per session and continue; manifest tracks skipped sessions |
| QMD embed slow on first run (downloads GGUF model, ~2GB) | Medium | Document in developer-guide; not a blocker, just a one-time wait |
| Session files deleted before ingest runs | Low | Claude Code keeps sessions 30 days; run ingest before that window; bronze is the safety copy |
| Duplicate entries despite QMD check | Low | Idempotency test in MVP-1-07, MVP-1-08, MVP-1-09 ACs; similarity threshold (0.85) tunable |
| hippo-remember skill not found in other projects | Low | Symlink approach tested in MVP-1-05 and MVP-1-06; fallback is manual `claude -p --working-directory ~/src/hippo` |
