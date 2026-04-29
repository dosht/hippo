# Hippo Developer Guide

This is the entry point for working on the Hippo codebase.

## Quick links

| Topic | File |
|-------|------|
| Project layout and data roots | this file (below) |
| Setup on a fresh machine | this file (below) |
| Coding standards | [coding-standards.md](coding-standards.md) |
| Hook authoring | [hooks.md](hooks.md) |
| Pipeline conventions | [pipeline.md](pipeline.md) |
| Schedule and launchd | [pipeline.md#schedules](pipeline.md#schedules) |
| Testing | [testing.md](testing.md) |
| Code review findings | [CODE_REVIEW.md](CODE_REVIEW.md) |

## Project layout

```
hippo-public/               -- the framework repo (git-tracked)
  scripts/
    hooks/session_start.py  -- Claude Code SessionStart hook
    ingest_v2.py            -- nightly ingest (active)
    ingest.py               -- v1 ingest (SUNSET, see CODE_REVIEW.md)
    compact.py              -- v1 silver compaction (SUNSET)
    extract.py              -- v1 gold extraction (SUNSET)
    manifest.py             -- shared manifest read/write helpers
    launchd/                -- plist files + install/uninstall scripts
  .claude/
    agents/memory.md        -- memory subagent definition
    skills/hippo-remember/  -- portable remember skill
  docs/
    developer-guide/        -- this directory
    product/                -- PRD, epics, kanban
    arch42/                 -- arc42 architecture records
    schemas/                -- YAML frontmatter schemas
  gold/
    entries/                -- gitignored; symlink or HIPPO_GOLD_DIR points here
    sample-entries/         -- tracked sample entries for schema reference

~/.hippo/                   -- data root (local only, never committed)
  config                    -- HIPPO_INGEST_FROM=YYYY-MM-DD
  sessions.jsonl            -- sidecar written by SessionStart hook
  manifest.jsonl            -- ingest state
  bronze/<project_id>/      -- raw session copies
  traces/<project_id>.jsonl -- extracted traces (milestone 7, not yet)
  logs/                     -- ingest.log, session_start.log, stderr logs

~/Library/LaunchAgents/     -- loaded plists (installed by launchd/install.sh)
```

### Why three separate roots?

- **Framework repo** is public, version-controlled, and shared across machines via git.
- **Data root (`~/.hippo/`)** holds personal session data and pipeline state. It is local-only. Committing it would leak conversation content.
- **Gold entries** (`HIPPO_GOLD_DIR`, default `gold/entries/` inside the repo) are personal knowledge. They live in the private `hippo` repo (separate from `hippo-public`), not here. The path is configurable so you can point `hippo-public` scripts at your private gold directory without hard-coding paths.

## Setup on a fresh machine

```bash
# 1. Clone
git clone https://github.com/mou/hippo-public ~/src/hippo-public

# 2. Create data root
mkdir -p ~/.hippo/logs

# 3. Set ingest cutoff (today's date; sessions before this are permanently skipped)
echo "HIPPO_INGEST_FROM=$(date +%F)" >> ~/.hippo/config

# 4. Register the SessionStart hook
# Add to ~/.claude/settings.json "hooks.SessionStart":
#   { "type": "command", "command": "python3 ~/src/hippo-public/scripts/hooks/session_start.py" }
# Or run the helper:
bash ~/src/hippo-public/scripts/install-hooks.sh   # installs git hooks only
# Hook registration is manual; see hooks.md for the exact settings.json shape.

# 5. Install the nightly launchd job
bash ~/src/hippo-public/scripts/launchd/install.sh

# 6. Verify
launchctl list | grep hippo
cat ~/.hippo/config
```

QMD and gold setup (only needed for the v1 gold pipeline, currently being sunset):

```bash
npm install -g @tobilu/qmd
./scripts/qmd-setup.sh
```

## Conventions

- Never use em dashes in any output. Use commas, periods, or parentheses instead.
- Gold entries use YAML frontmatter; see `docs/schemas/gold-frontmatter.md`.
- Bronze, silver, manifest, and logs are local-only. Never commit them.
- Commit messages are imperative, present-tense, scope-prefixed. See coding-standards.md.
  Example: `ingest: skip sessions without sidecar`.
- When a change touches the manifest schema, update `scripts/manifest.py` docstring and
  `docs/schemas/` in the same commit.

## Gold entry conventions

Gold entries are Markdown files with YAML frontmatter. See `gold/sample-entries/` for
the schema reference. Content guidelines:

1. Be concrete. Include specific commands, ports, file paths, and configuration values.
2. One topic per entry. Multiple unrelated topics become separate files.
3. Preserve near-misses. Things that almost worked and why they failed are often the
   most valuable entries.
4. Include source context. Mention when and why the knowledge was discovered.

Entry types: `operational`, `pattern`, `gotcha`, `decision-context`.
