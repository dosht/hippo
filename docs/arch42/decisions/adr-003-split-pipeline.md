---
id: adr-003
status: accepted
date: 2026-04-15
---

# ADR-003: Split Pipeline Between Desktop Tasks and Cloud Routines

## Context

The Hippo pipeline needs to run nightly. Ingestion requires local filesystem access (reading `~/.claude/projects/`). Reconciliation only needs the git repo. Claude Code offers two scheduling mechanisms: Desktop scheduled tasks (local) and Routines (cloud).

## Decision

Split the pipeline:
- **Desktop scheduled task** (local, daily): Handles ingest, compact, extract, QMD reindex, git commit and push. This step needs local session files.
- **Cloud Routine** (nightly, after local push): Handles reconciliation, feedback processing, promotion detection. This step only needs the git repo.

## Rationale

- Ingestion MUST be local (session JSONL files live in ~/.claude/projects/).
- Reconciliation can be cloud-based (only reads/writes gold entries in the git repo).
- Cloud Routines run even when the laptop is closed, which is ideal for nightly processing.
- Running reconciliation at night avoids competing with daytime interactive usage for Max subscription limits.
- Desktop tasks have access to local files, MCP servers, skills, and plugins.
- Routines clone the repo at run start, so they always have the latest gold entries.

## Alternatives Considered

- **n8n workflow**: Would work but adds external dependency. Routines are native to Claude Code.
- **All-local Desktop task**: Would work but requires the laptop to be on. Routines survive laptop shutdown.
- **All-cloud Routine**: Cannot access local session files for ingestion.
- **Cron job + claude CLI**: More fragile, no built-in monitoring.

## Consequences

- Two scheduling mechanisms to configure and monitor (Desktop task + Routine).
- The local Desktop task must complete and push before the cloud Routine runs. Use time-offset scheduling (e.g., local at 1 AM, cloud at 2 AM) or trigger the Routine via API after the local task pushes.
- Routines count against daily limits (15 for Max). We use 1 per night, so plenty of headroom.
- Desktop tasks only run when the Claude Desktop app is open and the computer is awake. If the laptop is off for a day, ingestion catches up on the next run (manifest offset tracking handles this).
