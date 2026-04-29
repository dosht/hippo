# Section 1: Introduction and Goals

## Purpose

Hippo is the agent's own hippocampus. It automatically captures episodic memory traces from AI
coding sessions and injects relevant traces into future sessions as context. No agent effort
required during productive work. Memory accrues passively; recall is automatic.

Named after the hippocampus, the brain region that consolidates short-term experience into
long-term memory.

## Core Principle

**Zero-effort write, automatic read.**

- Write path: every Claude Code session fires a `SessionStart` hook that writes identity metadata
  to a sidecar file. A nightly ingest copies session JSONL files to a durable bronze store.
  An episode extractor [QUEUED] distils traces from bronze.
- Read path: a `UserPromptSubmit` hook [QUEUED] injects the top-K relevant traces into the
  agent's context window before each prompt is processed.

## Quality Goals (Priority Order)

| Priority | Goal | Measure |
|----------|------|---------|
| 1 | Durability | No trace lost due to Claude Code 30-day GC |
| 2 | Plugin-noise-proof | Observer/plugin sessions never reach the trace store |
| 3 | Branch-deletion-proof | Thread identity survives branch deletion and recreation |
| 4 | Quota-graceful | Nightly run stops cleanly on quota exhaustion; resumes next night |
| 5 | Agent-readable | Injected trace bundle fits in 2000 tokens; each trace is terse and concrete |

## Stakeholders

| Role | Interest |
|------|----------|
| Agent (Claude Code) | Receives relevant past experience before each prompt |
| User | Zero maintenance overhead; memory improves over time without intervention |
| Future contributors | Clear separation between capture, consolidation, and recall layers |
