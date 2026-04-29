# Section 7: Deployment View

## Overview

Hippo runs entirely on the user's laptop as a set of local scripts co-located with Claude Code.
There is no cloud component, no daemon process, and no network listener.

```
macOS laptop
├── Claude Code (host)
│   ├── ~/.claude/settings.json          -- hook registrations
│   ├── ~/.claude/projects/              -- raw session JSONL source (Claude Code owned)
│   └── hooks fire in-process            -- SessionStart, UserPromptSubmit [QUEUED]
│
├── Hippo data root: ~/.hippo/
│   ├── config                           -- HIPPO_INGEST_FROM=2026-04-28
│   ├── sessions.jsonl                   -- sidecar (append-only, all sessions)
│   ├── manifest.jsonl                   -- ingest state (append-only, last-write-wins)
│   ├── bronze/<project_id>/             -- immutable raw session copies
│   │   └── <session_id>.jsonl
│   ├── traces/<project_id>.jsonl        -- extracted episodic traces [QUEUED]
│   ├── index/                           -- vector index [QUEUED]
│   └── logs/
│       ├── ingest.log
│       ├── ingest.stderr.log
│       └── session_start.log
│
├── Hippo framework: ~/src/hippo-public/
│   └── scripts/
│       ├── hooks/session_start.py       -- SessionStart hook (DONE)
│       ├── ingest_v2.py                 -- nightly ingest (DONE)
│       ├── launchd/
│       │   └── com.mu.hippo.ingest-v2.plist   -- schedule (DONE)
│       ├── extract_episodes.py          -- [QUEUED]
│       ├── consolidate.py               -- [QUEUED]
│       └── hooks/user_prompt_submit.py  -- [QUEUED]
│
└── launchd
    └── com.mu.hippo.ingest-v2           -- fires ingest_v2.py daily at 03:00 (DONE)
```

## Scheduling

| Job | Trigger | Script | Status |
|-----|---------|--------|--------|
| Nightly ingest | launchd, daily 03:00 | `scripts/ingest_v2.py` | DONE |
| Episode extraction | after ingest completes | `scripts/extract_episodes.py` | QUEUED |
| Consolidation pass | after extraction completes | `scripts/consolidate.py` | QUEUED |

The nightly jobs are chained: ingest runs first, then extraction reads the manifest for newly
bronze sessions. A simple shell wrapper in `scripts/nightly/` will orchestrate the chain once
extraction is implemented.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `HIPPO_HOME` | `~/.hippo` | Data root override |
| `HIPPO_INGEST_FROM` | read from `~/.hippo/config` | Cutoff date for ingest |

## Hook Registration

Hooks are registered in `~/.claude/settings.json` (user-level settings, not project settings):

```
hooks:
  SessionStart:
    - scripts/hooks/session_start.py
  UserPromptSubmit:          [QUEUED]
    - scripts/hooks/user_prompt_submit.py
```

The hook scripts are referenced by absolute path or path relative to the Claude Code working
directory. See `~/.claude/settings.json` for the live registration.

## Data Lifecycle

| Data | Location | Retention | Git-tracked |
|------|----------|-----------|-------------|
| Raw sessions | `~/.claude/projects/` | ~30 days (Claude Code GC) | No |
| Sidecar | `~/.hippo/sessions.jsonl` | Indefinite | No |
| Bronze | `~/.hippo/bronze/` | Indefinite | No |
| Manifest | `~/.hippo/manifest.jsonl` | Indefinite | No |
| Traces | `~/.hippo/traces/` | Until pruned | No |
| Index | `~/.hippo/index/` | Rebuildable | No |
| Framework code | `~/src/hippo-public/` | Git-tracked | Yes |
