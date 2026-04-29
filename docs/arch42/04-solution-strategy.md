# Section 4: Solution Strategy

## The Agent-Hippocampus Framing

Hippo treats itself as the agent's hippocampus: a memory organ that operates transparently in
the background, consolidating experience and surfacing relevant memories at the right moment.

The agent never manages memory. It simply works. Hippo observes, consolidates, and recalls.

This is a deliberate inversion of v1's design, where the agent had to explicitly invoke a memory
subagent to query for past knowledge. In v2, recall is automatic and happens before each prompt.

## Key Strategic Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Memory unit | Episodic trace (not session, not document) | Sessions are too coarse; documents lose causality. Traces preserve the `(action, feedback, adjustment)` unit of learning. See ADR-004. |
| Write path | Hook-driven, sidecar metadata only | No JSONL mutation. SessionStart hook is the sole writer of identity metadata. See ADR-005. |
| Read path | Context injection (not retrieval) | Retrieval requires the agent to ask. Injection happens automatically. See Section 6. |
| Noise filter | Sidecar gate + cutoff date | All sessions without a sidecar are untrusted. Pre-cutoff sessions permanently skipped. See ADR-007. |
| Thread identity | sha1(project_id + branch), indefinite gap | A branch deleted and recreated is the same thread. Acceptable for single-user use. See ADR-006. |
| Consolidation | Strengthen / decay / prune | Used traces grow stronger; unused traces decay below a threshold and are pruned. [QUEUED] |
| Storage | Flat JSONL per project for traces | Simple, appendable, no database dependency for the trace store. |

## What v1 Taught Us

v1's medallion pipeline (bronze -> silver -> gold) was valuable for surfacing the RL framing and
proving that session transcripts contain extractable knowledge. It also revealed the core problem:
the agent had to ask for memory, which means it only asked when it already suspected something was
relevant. Automatic injection removes that blind spot.

The v1 bronze layer and the sidecar/cutoff noise-filter strategy carry directly into v2. The
silver compaction step, gold markdown documents, and QMD retrieval are not carried forward.

## Deferred Strategies

- **Multi-harness ingestion** (Hermes, Codex): architecture supports it via `project_id` namespacing
  but only Claude Code sessions are captured today.
- **Feedback signal**: how to detect that a trace was used and close the consolidation loop.
  Default heuristic: first 3 agent turns mentioning the trace's content. Revisit if noisy.
- **Extractor model**: Haiku (cheap/fast) vs Sonnet (better deltas). Default: Haiku.
