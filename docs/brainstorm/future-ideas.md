# Future Ideas

Ideas discussed during brainstorming that were deferred for later exploration.

## Persistent Memory Agent Sessions

Instead of spawning a fresh `claude -p` for each memory query, maintain long-lived sessions per project-agent combination. The session accumulates context from previous queries, so related follow-up queries benefit from already-loaded context. Could use a pseudo JSONL file filtered by project and agent name.

**Why deferred**: Adds session lifecycle management complexity. Start with stateless queries. Revisit if query latency becomes a measured problem.

## Spiking-Network Activation for Reconciliation

Model the session graph like a spiking neural network. When a new session arrives, its "signal" (topics, embeddings) propagates through the graph with attenuation. Sessions that receive enough signal "spike" (get activated for reconciliation). This limits the reconciliation scope to semantically nearby sessions only.

**Why deferred**: A simpler spreading-activation or personalized PageRank approach achieves the same goal. The SNN framing adds complexity without clear benefit at our current scale. Revisit if the graph grows to 1000+ nodes.

## Recursive Session Querying

Allow a queried session to propagate the question deeper to related sessions, with the question rewritten at each hop. Depth-limited via an attenuation threshold.

**Why deferred**: Research (ERL, Not Always Faithful) suggests one-hop retrieval is sufficient. Multi-hop adds cost, latency, and signal degradation. Every practical system limits to one hop.

## ACP as Query Interface

Expose the memory system as an ACP server so non-Claude agents (Codex, Gemini CLI) can query it.

**Why deferred**: We're Claude-only for now. The subagent approach is simpler and sufficient. ACP becomes relevant when multi-vendor agent orchestration is needed.

## Auto-Promotion to CLAUDE.md / Skills

When a gold entry's query_count exceeds a threshold, automatically suggest (or create) a CLAUDE.md entry or a skill file so the knowledge is always loaded without a memory query.

**Why deferred**: Needs the feedback loop running first (Phase 3). The promotion logic depends on reliable query counting and staleness tracking. Build after reconciliation is stable.

## Obsidian Vault as Visualization Layer

Point Obsidian at the gold/entries/ directory to get free graph visualization of knowledge relationships via wikilinks. Use Obsidian's graph view for human browsing and review of the knowledge base.

**Why deferred**: Obsidian is a nice-to-have visualization, not a core requirement. The gold entries are already human-readable markdown. Add Obsidian when the knowledge base is large enough to benefit from visual exploration.

## Multi-Harness Bronze Ingestion

Add session adapters for Hermes (SQLite session store), OpenClaw (markdown memory files), Codex (session format TBD), and other agent harnesses. Each adapter normalizes to a common intermediate format.

**Why deferred**: Claude Code is the primary harness. Start there. Add adapters as needed when other harnesses are used regularly.

## PostgreSQL / Supabase Backend for QMD

Replace QMD's local SQLite with PostgreSQL + pgvector (possibly via Supabase) for cross-machine access without git sync. A community proof-of-concept exists (hubert-qmd).

**Why deferred**: QMD's local SQLite is sufficient for our scale. Git sync handles multi-machine. Revisit if real-time cross-machine access becomes a requirement.

## Compaction-Aware Session Splitting

During compaction, detect when a single session covers multiple unrelated topics and split it into separate silver artifacts. Conversely, merge multiple short sessions about the same topic.

**Why deferred**: Adds significant complexity to the compaction step. Start with one-to-one session-to-silver mapping. Revisit based on real-world data about session topic coherence.

## LightRAG / GraphRAG Integration

Replace the simple QMD retrieval with a full graph-based RAG system that captures entity relationships between knowledge entries. Enables multi-hop reasoning queries.

**Why deferred**: QMD's hybrid search is sufficient for hundreds of entries. GraphRAG adds infrastructure cost (needs a 32B+ parameter LLM for entity extraction). Revisit if retrieval quality degrades at scale.

## Curated Doc Staleness Detection

Compare gold entries against curated docs to detect when curated docs are outdated. For example, if gold entries consistently reference port 9333 but the developer-guide says 9222, flag the discrepancy.

**Why deferred**: Requires reliable reconciliation first. Build on top of the suggestion system in Phase 3+.
