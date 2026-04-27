---
id: adr-002
status: accepted
date: 2026-04-15
---

# ADR-002: QMD as the Retrieval Layer

## Context

Hippo needs a way to search gold entries by both keyword and semantic similarity. Options evaluated: building custom embedding/search, using LightRAG/GraphRAG, using Obsidian's search, or using QMD.

## Decision

Use QMD (by Tobi Lutke) as the retrieval layer. QMD provides hybrid search (BM25 + vector + LLM reranking) over markdown files, all running locally via SQLite and node-llama-cpp.

## Rationale

- Handles markdown natively (our gold entries are markdown with YAML frontmatter)
- Hybrid search combines keyword matching with semantic similarity, better than either alone
- Runs entirely locally. No cloud database, no API costs, no privacy concerns.
- Has a built-in MCP server for future integration if needed
- Has a library API (QMDStore) for programmatic use
- Incremental updates: new entries are indexed without rebuilding the entire index
- Active development, 17K+ GitHub stars, backed by Shopify CEO

## Alternatives Considered

- **LightRAG**: Requires a 32B+ parameter LLM for entity extraction. Overkill for our scale.
- **GraphRAG (Microsoft)**: Too heavy. Designed for large document corpora with complex entity relationships.
- **Custom embedding pipeline**: More work, less mature than QMD's hybrid approach.
- **Obsidian search**: Requires Obsidian to be running. Not headless-friendly.
- **PostgreSQL + pgvector**: Good for multi-machine, but adds infrastructure. QMD's SQLite is sufficient.

## Consequences

- QMD's SQLite index is local per machine. Multi-machine sync requires git-pulling gold entries and rebuilding the index.
- Requires ~2GB disk for GGUF models on first use.
- QMD does not support PostgreSQL natively. If we need centralized search later, we'd need to replace QMD or use the community pgvector adapter.
- Accepted: these tradeoffs are fine for a single-user, git-synced setup.
