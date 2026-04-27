# Hippo - Experiential Memory for AI Agents

Hippo is an experiential memory system that automatically captures knowledge from AI coding sessions (Claude Code, Codex, Hermes, etc.) and makes it queryable across all projects and agents. Named after the hippocampus, the brain region that consolidates short-term experiences into long-term memory.

## Core Principle

**Zero-effort write, intelligent read.** Agents never think about memory during productive work. Everything is captured automatically from session JSONL files. Knowledge is extracted offline, and agents query it via a dedicated memory subagent that returns direct answers (not documents).

## What Hippo Is NOT

- NOT a replacement for curated docs (arc42, developer-guide, playbooks, stories). Those are human-reviewed and enforced. Hippo complements them with experiential knowledge.
- NOT model fine-tuning. No weight updates. All learning happens through context injection.
- NOT a general knowledge base. Hippo captures operational knowledge learned through AI agent sessions.

## Architecture

Bronze/Silver/Gold medallion pipeline:
- **Bronze**: Raw session JSONL files. Immutable. Source of truth.
- **Silver**: Compacted sessions. Noise removed, decisions preserved.
- **Gold**: Markdown files with YAML frontmatter. Queryable via QMD. What agents actually read.

## Key Design Decisions

- **Skills-based, not MCP**: The system is powered by Claude Code skills and Python/TypeScript scripts, not a custom MCP server or CLI binary.
- **QMD for retrieval**: Local hybrid search (BM25 + vectors + reranking) over gold entries.
- **Subagent for queries**: A dedicated memory subagent runs in its own context, returns concise answers to the parent agent.
- **Routines for pipeline**: Claude Code Routines (cloud) and Desktop scheduled tasks (local) handle nightly ingestion and reconciliation.
- **Git-tracked gold**: Gold entries are version-controlled. Bronze/silver are local-only.
- **Agent-scoped views**: Memory entries have agent metadata. Each agent type (developer, tester, tech-lead) can filter to its own perspective, or access the full memory.

## Status

Early development. Pipeline scripts are not yet implemented. No build/test/lint tooling wired up yet. Today the repo is mostly docs, gold entries, and the subagent/skill definitions.

## Project Structure

```
hippo/
├── CLAUDE.md                    # This file
├── README.md
├── docs/
│   ├── product/                 # PRD, kanban, epics
│   ├── arch42/                  # System architecture (arc42 template)
│   ├── developer-guide/         # How to work on Hippo
│   ├── playbooks/               # Pipeline step playbooks
│   ├── knowledge/               # Curated knowledge notes
│   ├── schemas/                 # Data/frontmatter schemas
│   └── brainstorm/              # Research and future ideas
├── gold/
│   └── entries/                 # Knowledge entries (git-tracked)
├── manifest.jsonl               # Pipeline state tracking (aspirational, empty today)
├── feedback.jsonl               # Query feedback log (empty today)
└── .claude/
    ├── agents/memory.md         # Memory subagent definition
    └── skills/hippo-remember/   # Portable remember skill
```

## Conventions

- Never use em dashes in any output. Use commas, periods, or parentheses instead.
- Gold entries use YAML frontmatter with specific required fields (see docs/schemas/gold-frontmatter.md).
- All pipeline changes are git-committed with descriptive messages.
- Bronze, silver, and manifest are local-only (gitignored).
- Gold entries are personal data — they live in your private location pointed to by `HIPPO_GOLD_DIR`, not in this framework repo. See "Bring Your Own Gold" in README.md.
- The repo ships sample entries in `gold/sample-entries/` (tracked) for schema reference; `gold/entries/` is gitignored.
- QMD index is a derived artifact. Rebuild with `qmd update && qmd embed`.

## Setup

```bash
# 1. Install QMD (local search engine)
npm install -g @tobilu/qmd

# 2. Point Hippo at where you want to keep your gold (or skip — defaults to gold/entries/)
export HIPPO_GOLD_DIR=~/Documents/hippo-data/gold/entries
mkdir -p "$HIPPO_GOLD_DIR"

# 3. Register the gold entries collection and build embeddings
./scripts/qmd-setup.sh

# 4. Install the remember skill globally (symlink from user skills dir)
ln -s ~/src/hippo/.claude/skills/hippo-remember ~/.claude/skills/hippo-remember
```

## Quick Commands

```bash
# Query memory (via subagent or skill)
claude -p "What port does AI Browser use for CDP?" --working-directory ~/src/hippo

# Rebuild QMD index after gold changes
qmd update && qmd embed

# Check pipeline status (once the pipeline is writing to manifest.jsonl)
cat manifest.jsonl | jq -s 'group_by(.status) | map({status: .[0].status, count: length})'
```

## Agent Commands

Activate specialized agents for different tasks:

- `/team:developer` - Implement features from user stories
- `/team:tech-lead` - Review code and enforce standards
- `/team:architect` - Review architecture and design decisions
- `/team:tester` - Validate features and acceptance criteria
- `/team:scrum-planner` - Plan sprints with upfront story refinement (Scrum)
- `/team:kanban-planner` - Just-in-time story creation with pull-based workflow (Kanban)
- `/team:react-ui-designer` - Design beautiful, accessible React UIs
- `/team:mentor` - Train junior developers with adaptive tutorials