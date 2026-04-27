# Related Work

Research and tools that inform Hippo's design, surveyed April 2026.

## Academic Research

### Reinforcement Learning for LLM Reasoning

**DeepSeek-R1** (Jan 2025): Demonstrated that reasoning abilities can be incentivized through pure RL (RLVR) without human-annotated demonstrations. Used GRPO algorithm. Published in Nature.

**GRPO** (Group Relative Policy Optimization): Drops the critic/value model from PPO. Samples multiple answers, uses their relative quality as baseline. Computationally efficient. Became the default RL optimizer for reasoning models.

**DAPO** (ByteDance, Mar 2025, NeurIPS 2025): Open-sourced a reproducible RL pipeline with four fixes to GRPO: decoupled clipping, dynamic sampling, token-level loss, overlong reward shaping. Achieved 50% on AIME 2024 with Qwen-32B.

**"Does RL Really Incentivize Reasoning?"** (NeurIPS 2025 Oral): Key finding: RLVR doesn't create new reasoning patterns. It sharpens existing distributions. Base models at high pass@k outperform RL-trained models. RL is a "distribution sharpener," not a capability expander. This validates our approach: improve external knowledge, not model weights.

### Experiential Learning Without Weight Updates

**ExpeL** (Zhao et al., AAAI 2024): Agents learn from accumulated experience without parameter updates by extracting natural language insights from trial-and-error trajectories. Foundational paper for our approach.

**ERL** (ICLR 2026 MemAgents Workshop): Improved on ExpeL. Generates reusable heuristics from single-attempt trajectories. Key findings for Hippo: (1) heuristics transfer better than raw trajectories, (2) LLM-based retrieval outperforms embedding-only, (3) heuristics need concrete detail to be effective.

**"Not Always Faithful Self-Evolvers"** (Jan 2026): Agents are more faithful to raw experiences than condensed insights. When given both raw trajectory and abstract heuristic, agents follow the raw. Implication: gold entries need concrete commands, ports, file paths, not just abstract principles.

**MemRL** (Jan 2026): Self-evolving agents via RL on episodic memory. Found that near-miss failures are more valuable than clean successes. A critic model assigns higher value to memories that provide reusable guidance, including corrective heuristics from almost-successes.

### Memory Architectures

**Hindsight** (Dec 2025): Retain/Recall/Reflect operations turn conversational transcripts into structured, queryable memory. Separates facts from opinions. Reflect operation synthesizes across memories for higher-level insights. Maps to our reconciliation pipeline.

**"Memory in the Age of AI Agents"** survey (Dec 2025) + **ICLR 2026 MemAgents Workshop**: Comprehensive taxonomy of agent memory. Key framing: memory is the limiting factor for long-lived agents, not model capability. External, non-parametric memory is the practical path for improvement.

**AgentRR** (May 2025): Record-and-Replay paradigm for AI agent frameworks. Closest to our "sessions as replayable knowledge" idea.

## Production Systems

### Hermes Agent (Nous Research, 2025-2026)

Closest production system to Hippo. Key elements:
- Four-layer memory: always-on context, session history (SQLite + FTS5), procedural skills, user modeling
- Skills from experience: successful workflows become reusable markdown skill documents
- Progressive disclosure: agent sees skill names/descriptions first (~3K tokens), loads full content on demand
- Self-improvement loop: skills self-improve during use, periodic nudge prompts
- 32K+ GitHub stars as of April 2026

**Where Hippo differs**: Hermes relies on agent-curated memory (agent decides what to persist). Hippo captures everything automatically. Hermes has no offline reconciliation pipeline. Hermes doesn't separate curated from experiential knowledge.

### Letta / MemGPT (2023-2026)

OS-inspired tiered memory: Core (RAM, always in context), Recall (disk cache, searchable history), Archival (cold storage, tool-callable). Agent manages its own paging.

Recent developments:
- Sleep-time agents: async memory consolidation outside active conversation (validates our offline pipeline)
- Letta Code: git-backed memory for coding agents
- Conversations API: shared memory across parallel experiences

### QMD (Tobi Lutke, 2026)

Local search engine for markdown. BM25 + vector + LLM reranking, all local via node-llama-cpp. SQLite-backed. MCP server built in. Used as Hippo's retrieval layer.

### Obsidian + AI Agents (2026 ecosystem)

Obsidian vaults as knowledge layers for AI agents are a growing pattern. Plain markdown files are native to LLMs. Multiple MCP servers exist for vault access. Graph view provides relationship visualization. The obsidian-wiki project specifically ingests Claude Code and Codex session histories.

## Tools Evaluated

| Tool | Verdict | Rationale |
|------|---------|-----------|
| QMD | **Adopt** | Local hybrid search, handles our gold markdown natively, MCP-ready |
| Obsidian | **Consider later** | Good visualization layer, not needed for core functionality |
| LightRAG | **Defer** | Overkill for our scale, requires 32B+ LLM for entity extraction |
| GraphRAG (Microsoft) | **Defer** | Too heavy, designed for large document corpora |
| Mem0 | **Skip** | Framework-agnostic memory layer, but we don't need framework agnosticism |
| Letta | **Skip** | Full agent runtime, too opinionated for our skills-based approach |
