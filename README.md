# Hippo — Experiential Memory for AI Coding Agents

**Reinforcement-learning-style memory for Claude Code, without weight updates.**

Hippo automatically captures knowledge from your AI coding sessions and makes it queryable from any project. It treats each session as an implicit RL episode — actions, environment responses, and corrections — then distills those trajectories into reusable heuristics that any agent can retrieve.

Named after the hippocampus, the brain region that consolidates short-term experience into long-term memory.

## Why

When a Claude Code session ends, everything you and the agent figured out together is lost. Next session, the agent re-derives the same gotchas, asks for the same paths, retries the same dead ends.

Hippo turns those sessions into a queryable corpus. The agent learns from your past work, persistently, across all your projects.

The design draws on three findings from recent agent-memory research:
- **Near-miss failures teach more than clean successes** ([MemRL, 2026](https://arxiv.org)). Hippo's pipeline preserves the `(action, error, correction)` chain inline, not in a separate "problems" section.
- **Agents follow concrete details, not abstract summaries** ([Not Always Faithful, 2026](https://arxiv.org)). Gold entries inline real paths, errors, command flags, file:line refs.
- **LLM-based retrieval beats embedding-only** ([ERL, ICLR 2026](https://arxiv.org)). A dedicated memory subagent does the search and synthesis, returning ~200-word answers — not raw documents.

See [`docs/brainstorm/related-work.md`](docs/brainstorm/related-work.md) for the full literature review.

## How It Works

```
Claude session JSONL  ─►  bronze (raw)  ─►  silver (trajectory)  ─►  gold (heuristics)  ─►  QMD index  ─►  memory subagent
```

1. **You work normally.** Claude Code sessions auto-save to `~/.claude/projects/`.
2. **A nightly pipeline processes them.** Sessions are chunked (large ones split at user-turn boundaries), compacted into chronological `(action, response, adjustment)` trajectories, and distilled into reusable gold heuristics with frontmatter metadata.
3. **Reconcile keeps the corpus coherent.** Periodic clustering + LLM judge identifies duplicates and contradictions across newly-added entries.
4. **Any agent queries memory.** From any project, `/hippo-remember "<question>"` invokes a dedicated subagent that returns a concise, grounded answer.

## Architecture

Medallion pipeline (bronze → silver → gold) with a clean separation between raw experience and distilled heuristics:

| Layer | Shape | Tracked |
|---|---|---|
| **Bronze** | raw JSONL trajectory, immutable | local-only |
| **Silver** | denoised chronological trajectory | local-only |
| **Gold** | reusable heuristics with YAML frontmatter | **YOUR private repo** (BYOG, see below) |

Skills + Python scripts, not a custom server. QMD ([@tobilu/qmd](https://www.npmjs.com/package/@tobilu/qmd)) provides local hybrid retrieval (BM25 + vectors + reranking).

See [`docs/arc42/`](docs/arc42/) for the full architecture (arc42 template).

## Bring Your Own Gold (BYOG)

**Your gold entries are personal.** They contain operational knowledge from your real work — file paths, project names, debugging context, sometimes proper nouns from your stack.

This repo ships the **framework only**. Your data lives in a separate location (a private git repo, a plain dir under `~/Documents/`, an iCloud-synced folder — your call).

Configure where Hippo reads/writes your data via env vars:

```bash
# in ~/.zshrc
export HIPPO_GOLD_DIR=~/Documents/hippo-data/gold/entries
export HIPPO_RETIRED_DIR=~/Documents/hippo-data/gold/_retired
```

Or use symlinks if you prefer (no env var needed):

```bash
ln -s ~/Documents/hippo-data/gold/entries  ~/src/hippo-public/gold/entries
ln -s ~/Documents/hippo-data/gold/_retired ~/src/hippo-public/gold/_retired
```

If neither is set, Hippo defaults to `gold/entries/` inside this repo — fine for trying it out, but you should set `HIPPO_GOLD_DIR` before running the pipeline against your own sessions.

Sample entries demonstrating the schema live in [`gold/sample-entries/`](gold/sample-entries/).

## Quick Start

```bash
# 1. Prerequisites
npm install -g @tobilu/qmd
pip install -e .                                 # or just run via `python -m`

# 2. Clone (path must match HIPPO_HOME default in scripts/nightly/run.sh,
#    or override with: export HIPPO_HOME=/your/path)
git clone https://github.com/<you>/hippo-public ~/src/hippo-public
cd ~/src/hippo-public

# 3. Point Hippo at where you want to keep your gold (or skip — defaults to gold/entries/)
export HIPPO_GOLD_DIR=~/Documents/hippo-data/gold/entries
mkdir -p "$HIPPO_GOLD_DIR" gold/entries

# 4. Register the QMD collection (run once)
./scripts/qmd-setup.sh

# 5. Install the remember skill globally
ln -s ~/src/hippo-public/.claude/skills/hippo-remember ~/.claude/skills/hippo-remember

# 6. Register the SessionStart hook (required — writes sidecar metadata to
#    ~/.hippo/sessions.jsonl on every Claude Code session start; ingest gates
#    on this file, so without it no sessions are admitted to the pipeline).
#    Add to ~/.claude/settings.json under hooks.SessionStart:
#      { "command": "python3 /Users/<you>/src/hippo-public/scripts/hooks/session_start.py" }
mkdir -p ~/.hippo

# 7. Optional: set the ingest cutoff date. Sessions started before this date
#    are skipped at ingest. Defaults to 2026-04-28 if unset.
export HIPPO_INGEST_FROM=2026-04-28

# 8. Schedule nightly pipeline (macOS)
./scripts/launchd/install.sh                     # 21:30 first attempt + 23:30 retry
```

To query memory from any project:

```
/hippo-remember "What port does AI Browser use for CDP?"
```

The skill spawns a memory subagent that runs qmd, reads the matching gold entries, and returns a synthesized answer with source, confidence, and last-validated date.

## Manual Pipeline Run

If you want to invoke the pipeline by hand (instead of waiting for nightly):

```bash
# Full pipeline (ingest → compact → extract → reconcile cluster + judge)
./scripts/nightly/run.sh

# Apply approved reconcile proposals (NOT run nightly — review proposals first)
python -m scripts.reconcile apply --approve <c001,c003,...>
# or
python -m scripts.reconcile apply --approve-all
```

Stages can be run individually:

```bash
python -m scripts.ingest
python -m scripts.compact
python -m scripts.extract
python -m scripts.reconcile cluster [--full]
python -m scripts.reconcile judge
python -m scripts.reconcile apply [--approve <ids> | --approve-all]
```

## Conventions

- Gold entries use YAML frontmatter with required fields (see [`docs/schemas/gold-frontmatter.md`](docs/schemas/gold-frontmatter.md)).
- Bronze and silver are local-only and gitignored.
- The QMD index is a derived artifact; rebuild with `qmd update && qmd embed`.
- Reconcile mutations to gold get committed with `M4/reconcile: <N> merges, <M> retirements` in your data repo.

## Status

MVP-1 complete. Cross-project query, automatic ingestion, nightly reconciliation, all working end-to-end. See [`docs/product/epics/MVP-1/`](docs/product/epics/MVP-1/) for the full epic and [`hackathon-brief.md`](hackathon-brief.md) for the closeout sprint that shipped trajectory-shaped silver and the M4 reconcile pipeline.

## License

MIT — see [LICENSE](LICENSE).
