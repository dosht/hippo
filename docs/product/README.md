# Hippo - Product Requirements Document

## Vision

AI coding agents lose all experiential knowledge between sessions. Hippo solves this by automatically capturing session data as raw material, processing it offline into structured knowledge, and making it queryable by any agent in any project.

The result: an agent that worked with you on Supabase RLS configuration three weeks ago can surface that knowledge when a different agent in a different project encounters a similar challenge. No manual documentation required. No memory-write instructions during productive work.

## Problem

### Current Pain Points

1. **Session amnesia**: Every new Claude Code session starts from zero. Knowledge about tool configurations (e.g., "AI Browser uses CDP port 9333"), deployment gotchas, and codebase patterns vanishes when the session ends.

2. **Manual documentation burden**: Maintaining developer-guides, CLAUDE.md files, skills, and architecture docs is time-consuming. Telling the agent "update the docs" is non-deterministic and often produces incomplete or unfocused results.

3. **Cross-project knowledge silos**: A solution discovered in the Transgate frontend project is invisible to agents working on LingoChat or KenzNote, even though the underlying technology (React, Supabase, etc.) is the same.

4. **Context loading failures**: Agents either load too little context ("I now have the full picture" when they clearly don't) or too much (filling the context window with irrelevant architecture docs).

5. **Repeated explanations**: Explaining the same operational detail across sessions, across projects, across agents. If explained once, it should be available everywhere.

### What Exists Today

| Solution | Limitation |
|----------|-----------|
| CLAUDE.md | Requires manual curation. Limited in size. |
| claude-team knowledge tool | Agent must decide when to record. Non-deterministic. |
| Arc42 / developer-guide | Human-maintained. Expensive to keep current. |
| Claude's built-in memory | Shallow summaries. No cross-session reasoning. |
| RAG systems | Return documents, not answers. Miss indirect relevance. |

## Users

1. **Mustafa** (primary): Bootstrap founder running multiple projects with multiple AI agent types across different codebases.
2. **AI agents** (developer, tester, tech-lead, architect): Claude Code subagents that need operational knowledge to do their jobs without repeated instructions.
3. **Future team members**: Anyone who joins a project benefits from the accumulated experiential knowledge without reading hundreds of past session logs.

## Key User Stories

### Remembering (Read Path)

- As an agent, when I encounter an unfamiliar tool or configuration, I can query Hippo and get a direct, concise answer grounded in real past experience.
- As an agent, I can filter memory by my role (developer vs. tester) to get knowledge most relevant to my current task.
- As an agent, when I get a stale answer, Hippo warns me and suggests re-validation.
- As a user, I can query Hippo from the terminal to find how I solved something previously.

### Memorizing (Write Path)

- As a user, I never need to tell the agent to "remember" something. All session data is captured automatically.
- As a user, the nightly pipeline processes my sessions and extracts valuable knowledge without my intervention.
- As a user, I can see what was processed and when via the manifest.

### Self-Improvement

- As a system, frequently queried topics are automatically promoted to higher priority.
- As a system, stale operational knowledge is flagged and optionally re-validated.
- As a system, contradictions between sessions are detected and flagged.
- As a system, when a topic is queried enough times, I suggest adding it to CLAUDE.md or creating a dedicated skill.

## Non-Goals (v1)

- Real-time learning during a session (sessions learn naturally via tool feedback, Hippo operates offline)
- Modifying curated docs automatically (suggestions only, human decides)
- Multi-user support (single-user system for now)
- Cross-machine real-time sync (git sync is sufficient)
- Graph database or complex infrastructure (QMD + SQLite + git is enough)

## Success Metrics

1. **Time saved**: Reduction in "re-explaining" the same operational detail across sessions.
2. **Query usefulness**: Percentage of memory queries that return actionable answers (tracked via feedback).
3. **Coverage**: Percentage of sessions that produce at least one gold entry.
4. **Zero manual writes**: No memory_write or record_lesson calls needed during productive work.

## Phased Delivery

### MVP: End-to-End Write-Path to Read-Path Loop (2-3 weeks)

Delivers the full loop in a single workflow. Covers what were previously Phases 0, 1, and 2.
Hand-craft seed gold entries, set up QMD, build and validate ingest/compact/extract pipeline
scripts, install hippo-remember globally, and confirm a cross-project query returns a grounded
answer from an auto-extracted entry. A 5-session validation gate sits between building the pipeline
and running it against full history.

See docs/product/epics/HIPPO-MVP.md for stories and acceptance criteria.

### Post-MVP: Reconciliation + Routines (2 weeks)

Cross-session reconciliation detects confirmations, contradictions, and extensions across gold
entries. Cloud Routine handles nightly reconciliation without requiring the laptop to be on.
Feedback processing updates confidence scores. Auto-promotion suggests CLAUDE.md and skill
entries for high-frequency topics. Staleness detection flags entries past their policy.

### Scale + Multi-Harness (ongoing)

Session adapters for Hermes, Codex, and other agent harnesses. Agent-scoped views for filtered
queries by role. Obsidian vault as visualization layer. PostgreSQL backend for QMD if
cross-machine real-time access becomes a requirement.

## Project Progress

<!-- PROGRESS_START -->
**Overall Progress:** [████████████████░░░░] 82% (18/22 stories done)
*Last updated: 2026-05-03*

### Epic Progress

| Epic ID | Epic Name | Progress | Stories |
|---------|-----------|----------|---------|
| MVP-1 | End-to-End Write-Path to Read-Path Loop | [████████████████████] 100% (16/16 stories done) | 16/16 |
| MVP-2 | Quota Resilience and Cost Optimization | [██████░░░░░░░░░░░░░░] 33% (2/6 stories done) | 2/6 |

<!-- PROGRESS_END -->
