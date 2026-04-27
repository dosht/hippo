# Hippo - Developer Guide

## Prerequisites

- Claude Code with Max subscription
- QMD: `npm install -g @tobilu/qmd`
- Python 3.11+
- Git

## Setup

Run the setup script to register both QMD collections and generate embeddings:

```bash
./scripts/qmd-setup.sh
```

Or run the steps manually:

```bash
# Register gold entries as a QMD collection
# The --mask flag is required. Without it, qmd indexes all files including
# sample-gold-entry-format.md and inflates result counts.
qmd collection add ~/src/hippo/gold/entries --name hippo --mask "**/*.md"

# Register silver as a separate collection (used for MVP-1-08.5 read-path validation)
# silver/ does not exist until MVP-1-08 completes; run this command once it does.
qmd collection add ~/src/hippo/silver --name hippo-silver --mask "**/*.md"

# Generate embeddings
# First run downloads local GGUF models to ~/.cache/qmd/models (~2GB total:
# embeddinggemma-300M ~329MB, Qwen3-Reranker ~600MB, query-expansion ~1.28GB).
# Subsequent runs are fast (only new files are embedded).
qmd embed

# Verify the index
qmd status

# Symlink the remember skill globally
mkdir -p ~/.claude/skills
ln -s ~/src/hippo/.claude/skills/hippo-remember ~/.claude/skills/hippo-remember
```

After symlinking, any Claude Code session in any project can use the Hippo memory system.

## Global Skill Install

```bash
mkdir -p ~/.claude/skills
ln -s ~/src/hippo/.claude/skills/hippo-remember ~/.claude/skills/hippo-remember
```

This makes the hippo-remember skill available in Claude sessions in any project, not just Hippo.

## QMD Setup Script

`scripts/qmd-setup.sh` automates the collection registration and embedding steps above. It reads
`HIPPO_HOME` (defaulting to `~/src/hippo`) so it works on any machine without editing:

```bash
# Default path
./scripts/qmd-setup.sh

# Custom path
HIPPO_HOME=/path/to/hippo ./scripts/qmd-setup.sh
```

The script skips the `hippo-silver` registration if `silver/` does not exist yet, printing the
command to run once the directory is created by MVP-1-08.

## Project Layout

```
hippo/
├── scripts/            # Python workers (ingest, compact, extract, manifest helpers)
├── .claude/
│   └── skills/
│       ├── hippo-pipeline/     # Routine entry point; bundles compaction/extraction prompts
│       └── hippo-remember/     # Query skill (symlinked globally)
├── gold/
│   └── entries/        # Git-tracked knowledge entries (Markdown + YAML frontmatter)
├── bronze/             # Raw session JSONL copies. Local only, gitignored.
├── silver/             # Compacted sessions. Local only, gitignored.
├── manifest.jsonl      # Pipeline state for every session
└── feedback.jsonl      # Query feedback log
```

## Pipeline Stages

### Stage 1: Ingest

**Script:** `scripts/ingest.py`
**Trigger:** cron job or macOS Desktop scheduled task. Pure Python, no LLM.

Reads `~/.claude/projects/`, copies new session JSONL files to `bronze/`, appends a manifest row with `status: bronze` for each new session.

Subagent sessions (at `<project>/<session-uuid>/subagents/agent-*.jsonl`) are ingested
as separate manifest entries alongside top-level sessions. Each subagent entry has its own
`session_id` (derived from the agent filename, e.g. `agent-a7b3a2427ff7af6b7`), and is
linked to its parent via `parent_session` (the outer session UUID). The `agent` field is
populated from the sibling `.meta.json` file, and the `agent_task` field holds the
`description` from that file (truncated to 500 chars). This enables agent-scoped views:
filtering the manifest by `agent` shows all sessions a specific agent type ran, linked
to their parent context via `parent_session`.

### Stage 2: Compact

**Script:** `scripts/compact.py`
**Trigger:** `/hippo:pipeline` skill (nightly Routine).

Iterates manifest rows in `status: bronze`. For each one, shells out:

```bash
claude -p --max-turns 1 --no-mcp --model claude-sonnet-4-5 "<compact prompt>"
```

Expects JSON back. Python validates the response, writes to `silver/`, updates manifest to `status: silver`.

### Stage 3: Extract

**Script:** `scripts/extract.py`
**Trigger:** `/hippo:pipeline` skill, after compact.

Same pattern as compact. Iterates `status: silver` rows, shells out `claude -p`, expects JSON, validates frontmatter, writes gold entries, updates manifest to `status: gold`.

### Stage 4: Index

```bash
qmd update && qmd embed
```

### Stage 5: Commit

```bash
git add gold/ manifest.jsonl feedback.jsonl && git commit -m "pipeline run $(date +%F)"
```

**Reconcile** (cross-referencing gold entries for contradictions and confirmations) is planned post-MVP and not yet implemented.

## The Skill-as-Orchestrator Pattern

The `/hippo:pipeline` skill is intentionally thin. It exists to give the nightly Routine a named entry point and to bundle the compaction and extraction prompts as resource files (`.claude/skills/hippo-pipeline/resources/`). It does not run LLM calls itself.

All LLM calls happen inside the Python scripts via `claude -p --max-turns 1 --no-mcp`. The `--max-turns 1` flag is only available on the `claude -p` CLI, not in Task-tool subagents, which is why Python owns the iteration loop. Python also handles manifest state transitions, rate-limit backoff, JSON validation, and file writes. The skill delegates immediately to the scripts and returns.

## Gold Entry Conventions

### File Naming

Use the entry ID as the filename: `mem-ai-browser-cdp-setup.md`

### Required Frontmatter

Every gold entry must have all fields from the schema. See `gold/sample-gold-entry-format.md`.

### Content Guidelines

1. **Be concrete.** Include specific commands, ports, file paths, and configuration values. Agents ignore abstract principles but follow concrete details.
2. **One topic per entry.** If a session covered multiple unrelated things, create separate entries.
3. **Preserve near-misses.** Document things that almost worked and why they failed. These are often more valuable than clean successes.
4. **Include source context.** Briefly mention when and why the knowledge was discovered.

### Entry Types

- **operational**: How-to. "The AI Browser uses CDP on port 9333."
- **pattern**: Reusable approach. "When hitting CORS issues with Supabase Edge Functions, configure allowed origins in supabase/config.toml."
- **gotcha**: Known pitfall. "Railway deployments fail silently if EXPOSE port doesn't match PORT env var."
- **decision-context**: Rationale. "We chose LemonSqueezy over Stripe because of VAT handling."

## Testing

Python helpers (`scripts/manifest.py`, `scripts/ingest.py`, JSON validators) are unit-testable and should have tests covering state transitions and validation logic.

LLM output quality is validated manually. S8.5 (silver read-path validation) checks whether compacted silver is useful before extract is built. S10 (5-session end-to-end) validates the full pipeline on real sessions. Do not mock `claude -p` or attempt full-pipeline integration tests in CI.

## Adding a New Pipeline Stage

1. Write a Python script under `scripts/`.
2. Use `scripts/manifest.py` helpers for reading and updating manifest state.
3. Shell out to `claude -p --max-turns 1 --no-mcp --model claude-sonnet-4-5` with a prompt loaded from the skill's resource files.
4. Validate the JSON response in Python before writing any files.
5. Update `manifest.jsonl` with the new status after a successful write.

## Running the Pipeline Manually

```bash
# Ingest new sessions
python scripts/ingest.py

# Run compact + extract via the skill (triggers the Routine locally)
claude -p '/hippo:pipeline'

# Or run individual stages
python scripts/compact.py
python scripts/extract.py

# Reindex after gold changes
qmd update && qmd embed
```

## Git Hooks

`.git/hooks/` is not tracked by git and must be set up manually on each machine.
Run the installer once after cloning (or re-cloning) the repo:

```bash
bash scripts/install-hooks.sh
```

This copies `scripts/post-merge` to `.git/hooks/post-merge` and sets it executable.

### What the post-merge hook does

After every `git pull`, the hook checks whether any files under `gold/entries/` changed.
If they did, it runs `qmd update && qmd embed` to re-index the `hippo` QMD collection so
new entries are immediately searchable. If nothing under `gold/entries/` changed, it exits
silently with no work done.

The hook reads `HIPPO_HOME` (defaulting to `~/src/hippo`) so it works on any machine
without editing the script. To use a custom path:

```bash
HIPPO_HOME=/path/to/hippo bash scripts/install-hooks.sh
```

The `HIPPO_HOME` variable must also be set in your shell environment so the hook finds the
right directory at run time.

## Multi-Machine Workflow

1. Work on machine A. Sessions captured locally.
2. Nightly pipeline on machine A processes sessions, pushes gold to git.
3. On machine B: `git pull` (the post-merge hook re-indexes automatically if gold changed).

If the hook is not installed, run `qmd update && qmd embed` manually after pulling.

## Manifest Schema Notes

As of Phase A (MVP-1-14), the manifest records three additional provenance fields for
every new session: `cwd` (exact working directory), `git_branch` (first-seen git branch),
and `session_started_at` (ISO8601 timestamp of the first non-permission-mode record in the
JSONL). All three are nullable. Entries written before Phase A will have null values for
these fields and are not backfilled. Silver frontmatter and the extract prompt both propagate
these fields when present, so gold entries produced after Phase A may reference `git_branch`
and `session_started_at` as optional provenance metadata.

## Conventions

- Never use em dashes in any output. Use commas, periods, or parentheses instead.
- Commit messages describe what changed: "add CDP automation entry from session abc123" not "update files".
- Bronze and silver directories are gitignored. Local only.
- QMD index is derived. Never commit it. Rebuild after pulling gold changes.
