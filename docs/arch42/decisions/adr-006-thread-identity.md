---
id: adr-006
status: accepted
date: 2026-04-28
---

# ADR-006: Indefinite Thread Gap, Same Name = Same Thread

## Context

A thread represents a continuous line of work within a project on a given branch. The question
is: what counts as "the same thread" when sessions are separated by days or weeks, or when a
branch is deleted and recreated?

Options considered:
1. **Time-bounded threads**: sessions more than N hours apart start a new thread.
2. **UUID-per-branch-lifecycle**: a new thread UUID is assigned each time a branch is created
   (detected via branch creation timestamp).
3. **Name-stable threads**: `thread_id = sha1(project_id + ":" + branch)` always. No gap
   threshold. Same name = same thread, regardless of history.

## Decision

Option 3: `thread_id = sha1(project_id + ":" + branch)`. Indefinite gap. Same branch name
= same thread, even if the branch was deleted and recreated.

Implemented in `scripts/hooks/session_start.py` (`thread_id_for` function).

## Rationale

- Branch deletion + recreation with the same name is the common case for feature branches
  that are squash-merged and then reused. Collapsing these to the same thread is the
  behaviourally correct thing for a single-user system.
- Time-bounded threads add complexity: what is the right threshold? Different projects have
  different cadences. A 2-week sprint on one project vs a daily-commit project need different
  thresholds.
- UUID-per-lifecycle requires detecting branch creation events, which is not available from
  the `SessionStart` hook context.
- Simplicity wins. The edge case (a genuinely different conceptual thread under the same
  branch name) is rare enough to accept.

## Consequences

- If a branch is reused for an entirely different piece of work, traces from the old work
  survive in the same thread. The consolidation decay mechanism will eventually reduce their
  strength if they are not relevant to the new work.
- Multiple concurrent sessions on the same `project_id + branch` share a thread_id. The
  episode extractor must handle interleaved turns in this case.
