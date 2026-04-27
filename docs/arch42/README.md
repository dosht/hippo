# Hippo - Architecture

## Status

**Exists today**: All three pipeline scripts (`ingest.py`, `compact.py`, `extract.py`) implemented. QMD collection wired. Git post-merge hook installed. Memory subagent and hippo-remember skill in place. `scripts/status.sh` pipeline status command. 188 unit tests passing. MVP-1-13 (silver frontmatter propagation) shipped. MVP-1-14 (session metadata enrichment) and MVP-1-15 (subagent ingestion + bronze self-containment) implemented and pending sign-off.

**In flight**: MVP-1-11 (scoped pipeline validation on `kenznote-payments`, partial — 7/10 silvers compacted, 1 failed, no extract yet), MVP-1-12 (status command exists; doc cleanup pending).

**Known architectural defects (in active remediation)**:
- **Silver shape is wrong**: current compact prompt produces gold-shape thematic summaries (Decisions / Problems / Configurations / Near-Misses sections) instead of the intended RL-style chronological trajectory (action → environment feedback → adjustment). Two-distillation problem; gold is starved of causal signal.
- **Prompts misplaced**: `compact.py` and `extract.py` load their model prompts from `docs/playbooks/{compact,extract}.md`. Playbooks are a claude-team convention for `/playbook` slash commands, not a prompt store. Human-facing scaffolding bleeds into silver as narration noise.
- **Large sessions silently dropped**: `LARGE_SESSION_BYTES_THRESHOLD = 500_000` in `ingest.py` marks any session >500 KB as `skipped-large` with no bronze copy. In real projects this discards the most substantive sessions (4 of 5 top-level kenznote sessions lost). Chunked-bronze ingestion is the planned fix.
- **Echo-failure undetected**: large bronze sessions cause the model to echo the assistant's final closing message verbatim (<1% silver-to-bronze ratio). Current `RATIO_WARN_LOW = 0.40` threshold is miscalibrated (real good ratios are 3-6%) and does not catch this; poisoned silvers proceed to extract.
- **Retry coverage incomplete**: `compact.py` only retries on rate-limit errors; non-429 transient failures (rc=1, empty stderr) are marked `failed` immediately. `extract.py` has no retry loop at all.

**Deferred post-MVP**: reconcile (cross-session contradiction detection), team-of-agents memory views, multi-harness ingestion (Hermes, Codex), cross-machine real-time sync beyond git pull, feedback processing loop (`feedback.jsonl` write + read), promotion candidates, staleness flagging, Obsidian visualization, large-session chunking refinement beyond MVP minimum.

## System Overview

Hippo is a bronze/silver/gold data pipeline that transforms raw AI session transcripts into queryable experiential knowledge. It runs entirely within the Claude Code ecosystem using skills, scripts, and Claude Code Routines. No custom MCP servers, no separate CLI binaries, no external infrastructure beyond QMD (local search engine) and git.

## RL Framing

Hippo treats every Claude Code session as an implicit RL episode. This framing is load-bearing — it determines the shape of every layer.

A session is a sequence of `(s_t, a_t, r_t, s_{t+1})` tuples:

- `s_t` — **state**: codebase + session context at step t
- `a_t` — **action**: tool call, edit, or message produced by the agent (or, for user turns, an instruction or correction)
- `r_t` — **reward signal**: the environment's response — tool error, test failure, compile error, user correction ("no, that's wrong"), assertion failure
- `s_{t+1}` — updated state

The reward signal `r_t` carries the most teaching-rich information in a session. Errors and corrections are where the agent learns. Bronze must preserve them raw, silver must keep them inline at their point of occurrence in time, and gold must distill the patterns of `(a, r, adjustment)` triples that recurred. **No layer is allowed to extract `r_t` away from its `a_t` and the agent's response.** Doing so destroys the teaching value (see MemRL finding below).

## Research Foundations

The medallion architecture and the silver-shape constraint are research-driven. Implementation choices that contradict these findings are wrong by construction. Full survey: [`docs/brainstorm/related-work.md`](../brainstorm/related-work.md). Synthesis: [`docs/brainstorm/session-summary.md`](../brainstorm/session-summary.md).

| Finding | Source | Architectural consequence |
|---|---|---|
| RLVR sharpens existing distributions; doesn't create new reasoning. Base models at high pass@k outperform RL-trained ones. | "Does RL Really Incentivize Reasoning?" (NeurIPS 2025) | Validates the entire project: improve **external knowledge**, not weights. Hippo is the practical answer for closed-source models. |
| Sessions are implicit RL episodes; their experience is lost when the session ends. | Brainstorm § Core Insight | Drives **bronze = immutable raw archive** (the trajectory) and **silver = denoised chronological trajectory** (not themed facts). |
| Near-miss failures > clean successes. A critic assigns higher value to memories that provide corrective heuristics from almost-successes. | MemRL (Jan 2026) | Compact prompt must preserve near-misses **inline at point of occurrence** in the `(a, r, adjustment)` trajectory — not in a separate "Near-Misses" section. Ripping a near-miss from its context destroys its teaching value. |
| Agents are more faithful to raw experience than abstract summaries. Given both, agents follow the raw. | "Not Always Faithful Self-Evolvers" (Jan 2026) | Concrete values (paths, errors, commands, ports, file:line refs) live **inline** at their point of occurrence in silver, not in a "Configuration Details" aggregate. Gold entries must remain concrete; abstract heuristics get ignored at retrieval time. |
| Heuristics from experience transfer better than raw trajectories; LLM-based retrieval outperforms embedding-only; heuristics need concrete detail. | ERL (ICLR 2026 MemAgents Workshop) | Validates the medallion split: silver preserves the raw-ish single-attempt experience, gold extracts reusable heuristics. The two stages must do **distinct** work. The memory subagent's LLM-based synthesis is preferred over raw RAG. |
| Sessions as replayable knowledge — record-and-replay paradigm. | AgentRR (May 2025) | Confirms bronze's role as the immutable replay archive; chunking must preserve replay coherence (cuts at user-turn boundaries, not mid-`(a, r)` pairs). |

## Data Flow

```
Raw Sessions (JSONL)                          Working Agent
~/.claude/projects/*/sessions/*.jsonl         (any project)
        │                                          │
        │ Desktop scheduled task                    │ queries via subagent [exists]
        ▼                                          ▼
┌──────────────┐                          ┌──────────────────┐
│   Bronze      │                          │ Memory Subagent   │
│   (raw copy)  │                          │ (exists)          │
│   gitignored  │                          │                   │
└──────┬────────┘                          │  1. qmd search    │
       │ compact.py                        │  2. read entries  │
       ▼                                   │  3. synthesize    │
┌──────────────┐                          │  4. return answer │
│   Silver      │                          └────────┬──────────┘
│   (compacted) │                                   │
│   gitignored  │                                   ▼
└──────┬────────┘                          Concise answer returned
       │ extract.py                        to parent agent's context
       ▼
┌──────────────┐
│   Gold        │
│   (knowledge) │◄── QMD indexes this [exists]
│   git-tracked │
└───────────────┘
```

## Bronze Layer

**Source**: Claude Code session JSONL files from `~/.claude/projects/`.

**Content**: Complete session transcripts including user messages, agent responses, tool calls, tool results, thinking blocks. Subagent sessions are separate files linked via parentUuid.

**Storage**: Copied to `bronze/` directory (gitignored). The [manifest](../../scripts/manifest.py) tracks which sessions have been processed; the canonical schema lives in the frozen schema comment block at the top of `scripts/manifest.py` and is the authoritative source for field names, types, and nullability.

**Retention**: Claude Code deletes sessions after 30 days by default. The nightly ingestion copies them before deletion. The manifest tracks the latest ingestion offset so each run picks up only new sessions.

**Agent metadata**: Each JSONL session includes the agent name/type (developer, tester, tech-lead, etc.) when spawned as a subagent. This metadata is preserved through the pipeline and becomes a filterable property on gold entries.

### Bronze Self-Containment

**Principle**: Bronze is the immutable, complete archive of all source-side artifacts. The manifest (`manifest.jsonl`) is a derived index for fast filtering and status tracking -- it is never the sole source of provenance metadata.

Future stages (compact, extract) may run remotely via Claude Routine or on a different machine without access to `~/.claude/projects/`. Any field not copied into bronze at ingest time is potentially lost forever after Claude Code's 30-day source GC. Therefore:

- Every file that carries information not already encoded in the JSONL transcript must be copied into bronze alongside the JSONL.
- The canonical example: subagent sessions have a sibling `.meta.json` file (`{"agentType": "...", "description": "..."}`) in the source directory. Ingest copies this file into `bronze/<harness>/YYYY-MM-DD_<parent>_<agent-id>.meta.json` alongside the JSONL. The `meta_path` manifest field records the bronze copy's absolute path.
- If `.meta.json` is absent in the source (rare; auto-generated subagents), nothing is copied and `meta_path` stays null. The JSONL still ingests normally.
- Top-level sessions have no `.meta.json` sidecar; `meta_path` is always null for them.

**Future stages must be designed to run with only `bronze/` and `manifest.jsonl` accessible.** No stage after ingest should read from `~/.claude/projects/` or any other source-side path.

**Harness namespacing**: Bronze files are written to `bronze/<harness>/` (e.g. `bronze/claude-code/`) so that future ingestion sources (Codex, Hermes, etc.) can coexist without filename collisions. The `DEFAULT_BRONZE_DIR` constant (`bronze/`) is the parent root; all per-session writes resolve under the harness subdir.

### Large Session Handling (in active remediation)

Today: any session whose JSONL exceeds `LARGE_SESSION_BYTES_THRESHOLD = 500_000` bytes is marked `skipped-large` in the manifest and no bronze file is written. This was intended as a guardrail against `claude -p` failing on oversized inputs, but in practice it discards the most substantive sessions in active projects (in the kenznote-payments scope, 4 of 5 top-level sessions and 1 subagent fell in this bucket).

Planned fix: **chunked bronze**. At ingest time, sessions over a soft target (~250 KB) are split at user-turn boundaries into parts. Bronze layout mirrors the existing subagent pattern: each part is its own JSONL with a shared session-level `.meta.json`. New manifest fields `part_index` (1-based) and `total_parts` link parts back to a shared `parent_session`. Compact processes parts independently, with the prompt receiving `part K of N` so the model summarizes a slice without referencing forward or backward. A hard skip ceiling (~10 MB) covers pathological cases and is logged as `skipped-pathological`, not silently dropped.

This change must land before the trajectory prompt rewrite so the new prompt is calibrated against realistic chunk sizes from day one, not the legacy 500 KB cap.

## Silver Layer

**Input**: Bronze JSONL session (or one part of a chunked bronze session).

**Intended shape (target architecture)**: a **denoised RL-style chronological trajectory**. See § RL Framing above for the underlying `(s, a, r, s')` model.

Trajectory event format invariants (constraints on the compact prompt):

- Each event records an action (by user or agent), the environment's response (tool output, error, test result, user reaction), and the agent's adjustment.
- Events are emitted in time order. Causality is preserved across event boundaries.
- Concrete values (paths, errors, commands, ports, file:line refs) appear **inline at their point of occurrence** — never aggregated into a separate Configurations / Decisions / Near-Misses / Open Questions section.
- Near-misses appear inline as `(action, "almost worked because Y", adjustment)` triples — not in a dedicated section. The whole triple is the unit of learning (MemRL).
- For chunked sessions: each part declares `part K of N` at its head; the prompt instructs the model to summarize this slice only (no forward/backward references). Extract reads parts in `part_index` order and treats them as a continuous trajectory.
- No preamble narration ("I'll compact this session…"). Output begins with the first event.

**Current shape (defective)**: the prompt loaded from `docs/playbooks/compact.md` produces a thematic summary with sections (Key Decisions, Problems and Solutions, Configuration Details, Near-Misses and Gotchas, Open Questions). This is gold-shape, not silver-shape — it denormalizes the session into facts and destroys causal/temporal order. See Status section above. Remediation is in flight.

### Echo-Failure Detection

When bronze input is large enough that the model can't compact it within `--max-turns 1`, the failure mode is to **echo the assistant's final closing message** verbatim instead of producing a real summary. Observed silvers in this mode have <1% silver-to-bronze ratio.

Detection is the responsibility of two complementary mechanisms:

- **Chunking (M1) eliminates the precondition**: bronze parts cap at ~250 KB target / ~350 KB hard, well below the size where echo-failure manifested in real runs.
- **Recalibrated ratio thresholds catch any residual case**: `RATIO_WARN_LOW` is set to ~1% (echo-failure floor) and `RATIO_WARN_HIGH` to ~25% (insufficient-compression ceiling for trajectory output). Old values (40% / 90%) reflected the wrong target (thematic output) and tripped on every successful compaction. Threshold breaches log a warning and tag the manifest entry; the entry stays `silver` but is flagged for review before extract.

Until reconcile and `feedback.jsonl` are wired (post-MVP), these thresholds are the **only automated detection layer for silver quality**. Everything else is human eyeball.

### Compact Retry Coverage

`scripts/compact.py:call_claude_compact` retries on rate-limit signatures (rc=429 or stderr containing "429" / "rate limit"). All other non-zero exits — including the empty-stderr `rc=1` failure mode observed in the kenznote-payments run — go directly to `failed` with no retry.

**Planned change**: extend `_is_rate_limit_error` semantics to a broader `_is_transient_error` predicate. Empty-stderr `rc!=0` is treated as transient and retried with the same exponential backoff (2s, 4s, 8s, max 3 retries). Genuinely fatal errors (malformed prompt, unknown flag) still fail immediately because they produce non-empty stderr with a recognizable signature. `extract.py` gains an equivalent retry loop (currently has none).

**Process**: A compaction script (Python, `scripts/compact.py`) reads the JSONL and calls `claude -p --max-turns 1 --model claude-sonnet-4-5` with the compaction prompt. The prompt is loaded from a file path held in `DEFAULT_PROMPT` (today: `docs/playbooks/compact.md`; target: `scripts/prompts/compact.md` — see Pipeline Orchestration).

**Output**: Compacted markdown in `silver/` (gitignored). Real-world good ratios cluster at 3-6% of bronze size; ratios <1% indicate echo-failure (model echoed the assistant's final closing message instead of compacting).

**Model choice**: `claude-sonnet-4-5` via Max subscription (offline, cost-tolerant, runs at night).

## Gold Layer

**Format**: Markdown files with YAML frontmatter in `gold/entries/`.

**Frontmatter schema**:

```yaml
---
id: mem-unique-identifier
type: operational | pattern | gotcha | decision-context
topics: [list, of, topic, tags]
projects: [all | project-id-1, project-id-2]
agents: [all | developer, tester, tech-lead]
confidence: high | medium | low | verified
source_sessions: [session-uuid-1, session-uuid-2]
created: 2026-04-15
last_validated: 2026-04-15
last_queried: null
query_count: 0
staleness_policy: 30d | 60d | 90d | never
supersedes: []
---
```

**Entry types**:
- **operational**: Concrete how-to knowledge. Ports, commands, configurations.
- **pattern**: Reusable approaches. "When X happens, do Y because Z."
- **gotcha**: Things that go wrong and how to fix them.
- **decision-context**: Why something was done a certain way.

**Retrieval**: QMD indexes the gold/entries/ directory. Hybrid search (BM25 + vector + reranking) finds relevant entries. The memory subagent reads them and synthesizes answers.

## Query Architecture

```
Parent Agent (working on a task)
    │
    │ delegates to memory subagent
    ▼
Memory Subagent (.claude/agents/memory.md)
    │
    │ runs in own context window (Haiku model)
    │
    ├── 1. Bash: qmd query "{question}" --collection hippo --json
    ├── 2. Read: top matching gold entry files
    ├── 3. Reason: synthesize concise answer from entries
    └── 4. Return: answer + confidence + sources to parent
    │
    ▼
Parent receives ~50-100 token answer
(not the full entry documents)
```

The memory subagent's own session is also captured as a JSONL file. This creates an automatic feedback loop: frequently queried topics accumulate more memory-query sessions, which the pipeline uses as an importance signal.

### Architecture Validation Gate

The single test that proves the full medallion works end-to-end is a **cross-project query**: from a non-Hippo project (e.g., kenznote, Transgate frontend), `/hippo-remember <real operational question>` returns a grounded answer sourced from auto-extracted gold (not hand-crafted seed entries).

This test exercises every layer in sequence: bronze copy + chunking → silver trajectory → gold heuristic → QMD index → memory subagent retrieval → LLM-mediated synthesis → cross-project skill invocation. A passing cross-project query is the load-bearing acceptance criterion for MVP-1 and the canonical regression test for any future architectural change.

### Feedback Log (`feedback.jsonl`)

`feedback.jsonl` (repo root, git-tracked, currently empty) is the designed write target for query-time feedback: append-only JSONL where each line records whether a returned gold entry was actually useful (`{entry_id, query, useful, note, ts}`). It is the **reward signal** that closes the loop between the read path (memory subagent / hippo-remember) and gold maintenance (reconcile).

**Status**: file exists as an interface contract; no code writes it (memory subagent and hippo-remember skill do not yet append) and no code reads it (reconcile is post-MVP). The schema is fixed now so the format does not need to change once the loop is wired. For MVP-1, treat as a placeholder — gold quality is judged at write time by extract; once feedback.jsonl is populated, gold quality becomes judged at read time by actual usage (entries that get used get promoted, entries that get negative feedback lose confidence).

**What catches a bad silver / bad gold today**: nothing automated, except the recalibrated ratio thresholds in compact (Silver Layer § Echo-Failure Detection). Until reconcile and feedback.jsonl are wired, silver and gold quality are validated only by human eyeball at extract time and at cross-project query time. This is acceptable for MVP-1 because the corpus is small (one project's sessions) and a human is in the loop. Post-MVP, the feedback loop becomes the primary quality detector and ratio thresholds drop to a guardrail role.

## Pipeline Orchestration

**Ingest** runs as a local script only (`python scripts/ingest.py`), triggered by a Desktop scheduled task or manually. It requires access to `~/.claude/projects/` and cannot run in the cloud. Python owns iteration, manifest state, and file writes.

**Compact and extract** run as Claude Code Routines. The entry point is the `/hippo:pipeline` skill, which is a thin Routine wrapper. The skill invokes `scripts/compact.py` and `scripts/extract.py` as Python subprocesses. Both use `claude -p --max-turns 1 --model claude-sonnet-4-5`. Python owns backoff, JSON validation, manifest updates, and file writes. The skill does not contain pipeline logic; it is the scheduling entry point only.

**Prompt location**: model prompts for `compact.py` and `extract.py` belong in `scripts/prompts/{compact,extract}.md` as pure instruction bodies. Today they are sourced from `docs/playbooks/{compact,extract}.md`, which conflates two concerns: the human-facing pipeline-procedure documentation and the model instruction body. The misuse causes narration artifacts in silver output ("I'll compact this session following the playbook guidelines you provided") because the model receives the human framing as part of its instructions. Relocating prompts is a planned in-flight change.

**What a "playbook" actually is**: a claude-team convention for `/playbook` slash commands. Specifically:

- A playbook is **deterministic** — given the same arguments, the same agent will perform the same procedure.
- A playbook is **agent-callable** — invoked by a sub or main agent via the `/playbook <name>` command, not read by a Python script.
- A playbook describes a **repeatable task** (e.g., "review a PR", "release a version") with explicit inputs, steps, and outputs.

Compact and extract are neither: they are non-deterministic LLM completions invoked from Python via `claude -p`, not procedures the agent executes. They are **prompts**, not playbooks. Misuse of `docs/playbooks/` for compact/extract bleeds the human-procedure framing into the model's input. Future contributors must not place `claude -p` prompt bodies in `docs/playbooks/`. The `scripts/prompts/` directory is the correct location.

**Reconcile** is deferred post-MVP. MVP-scale deduplication is handled by `extract.py` running `qmd query` with a 0.85 similarity threshold before writing each new entry — this is single-shot per-entry dedup, not cross-corpus reconciliation. The full reconcile responsibilities (confirms / contradicts / extends / obsoletes / feedback processing / promotion candidates / staleness) are sketched in `docs/brainstorm/reconcile-design.md` (design-only; no `scripts/reconcile.py` exists) and listed individually in the MVP-1 epic's Out-of-Scope section.

**Local ingestion flow**:
1. Scan `~/.claude/projects/` for JSONL files not yet in manifest
2. Copy new sessions to `bronze/`
3. Run compaction on new bronze sessions via `compact.py`, produce silver
4. Extract gold entries from silver via `extract.py`
5. `extract.py` calls `qmd update --collection hippo && qmd embed --collection hippo` once at run end (not per entry)
6. Git commit and push gold changes
7. Manifest is updated per-session throughout by each script

**Cloud reconciliation**: deferred post-MVP.

## Relationship to Curated Docs

```
Curated Docs (authoritative)          Hippo (experiential)
arc42, developer-guide,               gold/entries/
playbooks, stories, ADRs

    │                                      │
    │ Hippo reads curated docs             │ Hippo may SUGGEST
    │ as reference context                 │ updates to curated docs
    │                                      │ via gold/suggestions/
    ▼                                      ▼
    Agent uses both:                   Human reviews suggestions
    curated for rules/conventions,     and decides whether to
    Hippo for experiential knowledge   update curated docs
```

Hippo NEVER modifies curated docs automatically.

## Multi-Machine Sync

Gold entries sync via git (push/pull). Each machine runs its own QMD instance pointed at the local gold/entries/ directory. After `git pull`, run `qmd update && qmd embed` to rebuild the local index. A git post-merge hook automates this.

Bronze/silver are local to each machine. The pipeline processes them locally and pushes gold to git. Gold is the convergence point.

## Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Retrieval | QMD | Local hybrid search, MCP-ready, handles markdown natively |
| Storage (gold) | Git + filesystem | Diffs, history, reverts, human-readable |
| Storage (bronze/silver) | Filesystem | Large, temporary, not worth git-tracking |
| Index | QMD SQLite | Derived, rebuildable, local per machine |
| Pipeline compute | Claude Code (Max subscription) | Skills + scripts, no API costs |
| Pipeline entry point | `/hippo:pipeline` skill (Routine) | Thin wrapper; Python scripts own iteration and state |
| Scheduling (local) | Desktop scheduled tasks | ingest.py only; requires access to local session files |
| Scheduling (cloud) | Claude Code Routines | compact + extract via `/hippo:pipeline` skill; runs without laptop |
| Pipeline scripts | Python (`scripts/ingest.py`, `compact.py`, `extract.py`, `status.sh`) | Shell out to `claude -p --max-turns 1 --model claude-sonnet-4-5`; Python owns backoff, manifest, file writes |
