# Brainstorm Session Summary

**Date**: 2026-04-15
**Participants**: Mustafa + Claude (claude.ai conversation)
**Duration**: Extended multi-turn session covering RL research, memory architecture design, and implementation planning.

## Starting Point

The conversation began with a literature review of reinforcement learning for LLMs (RLVR, GRPO, DAPO, agentic RL). The key finding was that current RL methods don't teach models new reasoning. They sharpen existing distributions. This led to a practical question: if we can't update model weights (closed-source models, no training infrastructure), how do we make agents learn from experience?

## Core Insight

AI coding sessions are already implicit RL episodes. The agent acts, the environment responds (code runs, tests pass/fail, errors appear), and outcomes are observable. The problem is that this experience is lost when the session ends. The solution: capture all sessions as raw data, process them offline into structured knowledge, and make that knowledge queryable by future sessions.

## Key Design Decisions Made

1. **Zero-effort write path**: The agent never decides what to remember. All session JSONL files are captured automatically. Knowledge extraction happens offline via a pipeline. This was chosen because every other memory system (Hermes, Letta, Claude's built-in memory) relies on the agent to decide what's worth persisting, which is non-deterministic and loses information.

2. **Bronze/Silver/Gold medallion architecture**: Borrowed from data engineering. Bronze is raw sessions (immutable). Silver is compacted sessions (noise removed). Gold is structured knowledge entries (markdown with YAML frontmatter, queryable via QMD). This maps to the ERL research finding that distilled heuristics transfer better than raw trajectories.

3. **LLM-mediated retrieval, not RAG**: The memory system returns direct answers, not document chunks. A dedicated memory subagent searches QMD, reads relevant gold entries, synthesizes an answer in its own context window, and returns only the concise answer to the parent agent. This prevents context pollution and gives better answers than cosine-similarity document retrieval.

4. **Skills-based architecture, not MCP/CLI**: Instead of building a custom MCP server or CLI tool, the entire system is a claude-team project with skills and scripts. Claude Code is both the runtime and the development environment. This dramatically reduces code, testing, and maintenance burden.

5. **Curated docs and experiential memory are separate systems**: Arc42, developer-guide, playbooks remain human-reviewed and authoritative. Hippo is experiential and self-improving. Hippo may suggest updates to curated docs but never modifies them directly.

6. **Memory subagent sessions create a feedback loop**: When the memory subagent queries gold entries, that query itself is a JSONL session. The pipeline processes these memory-query sessions to derive importance signals. Topics queried frequently get promoted. Topics never queried get archived. This is analogous to spaced repetition: reviewing a memory strengthens it.

7. **Agent-scoped views**: Each gold entry carries agent metadata (developer, tester, tech-lead). Agents can filter to their perspective for more relevant results, or access the full memory for cross-role knowledge.

8. **Claude Code Routines for nightly pipeline**: Just released (April 14, 2026), Routines run on Anthropic's cloud infrastructure on a schedule, using the Max subscription. Perfect for nightly ingestion and reconciliation without affecting daytime rate limits.

## Research That Informed the Design

- **ERL (ICLR 2026)**: Heuristics from experience transfer better than raw trajectories. LLM-based retrieval outperforms vector-only retrieval.
- **"Not Always Faithful" (Jan 2026)**: Agents follow raw experiences more than abstract summaries. Gold entries need concrete detail, not just principles.
- **MemRL (Jan 2026)**: Near-miss failures are more valuable than successes. Don't discard failed attempts in compaction.
- **Hermes Agent**: Progressive disclosure (show index first, load on demand) is a smart optimization. Procedural memory (skills from experience) is the right paradigm.
- **Letta/MemGPT**: Sleep-time agents (async memory consolidation) validate our offline pipeline approach.
- **Hindsight**: Separate facts from opinions. The retain/recall/reflect model maps to our ingest/query/reconcile.

## Ideas Explored But Deferred

See `future-ideas.md` for the full list. Key deferred ideas: spiking-network activation for reconciliation routing, persistent memory agent sessions for caching, ACP as query interface, Obsidian vault as visualization layer, PostgreSQL backend for QMD.
