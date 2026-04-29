# Section 12: Glossary

| Term | Definition |
|------|------------|
| **Bronze** | Immutable copy of a raw Claude Code session JSONL file. Stored in `~/.hippo/bronze/<project_id>/`. Never modified after initial copy. If the source changes, the bronze copy is overwritten and the manifest status is set to `stale`. |
| **Consolidation** | Nightly process that adjusts trace `strength` values: strengthens traces that were recently used, decays unused traces, prunes traces below a threshold. [QUEUED] |
| **Cutoff** | The date stored as `HIPPO_INGEST_FROM` in `~/.hippo/config`. Sessions started before this date are permanently skipped. Set once at setup time; never moved backward. |
| **Episode** | A unit of experience extracted from a session. In v1 this was a gold document; in v2 it is one or more traces. |
| **Episode extractor** | The [QUEUED] script (`scripts/extract_episodes.py`) that reads bronze sessions and emits traces into the trace store. |
| **Gold** | v1 term. Markdown files with YAML frontmatter in `gold/entries/`, queried via QMD. Not used in v2. Historical artefact. |
| **Hippocampus** | The brain region that consolidates short-term experience into long-term memory. Hippo's namesake and architectural metaphor. |
| **Ingest** | The process of copying qualifying session JSONL files from `~/.claude/projects/` to the bronze store. Implemented in `scripts/ingest_v2.py`. |
| **Manifest** | `~/.hippo/manifest.jsonl`. Append-only JSONL tracking ingest and extraction state per session. Last-write-wins on `session_id`. Status values: `bronze`, `stale`, `extracted`, `skipped-no-sidecar`, `skipped-cutoff`. |
| **project_id** | A 16-character hex string derived as `sha1(remote_url)` or `sha1("local:" + repo_root)`. The namespace for bronze files and traces. Null if the working directory is not a git repo. |
| **Recall** | The process of injecting relevant traces from the trace store into the agent's context window before a prompt is processed. Implemented via the `UserPromptSubmit` hook. [QUEUED] |
| **SessionStart hook** | A Claude Code hook (`scripts/hooks/session_start.py`) that fires at the start of every session and writes a sidecar record. The source of project and thread identity metadata. |
| **Sidecar** | `~/.hippo/sessions.jsonl`. Append-only JSONL file written by the `SessionStart` hook. One record per session (or more if the hook fires multiple times). The trust gate: sessions without a sidecar entry are not ingested. |
| **Sidecar gate** | The ingest filter that skips any session without a matching record in `~/.hippo/sessions.jsonl`. The primary mechanism for excluding plugin-spawned and pre-hook sessions. |
| **Silver** | v1 term. Compacted session summaries produced by `compact.py`. Not used in v2. Historical artefact. |
| **Strength** | A numeric value (float, 0.0-1.0+) on each trace indicating how relevant and recently-used it is. Set to 1.0 on creation. Modified by consolidation. Traces below a prune threshold are deleted. |
| **thread_id** | A 16-character hex string derived as `sha1(project_id + ":" + branch)`. Groups sessions on the same branch of the same project into a continuous thread of work. Null for detached HEAD or no-git sessions. |
| **Trace** | The atomic unit of memory in v2. A JSON object with fields: `id`, `type`, `cues`, `body`, `strength`, `thread_id`, `project_id`, `session_id`, `ts`, `last_used`. Types: `negative`, `fix`, `preference`, `directive`. |
| **Trace store** | `~/.hippo/traces/<project_id>.jsonl`. Flat JSONL file per project containing all extracted traces for that project. |
| **UserPromptSubmit hook** | A Claude Code hook [QUEUED] that fires before each user prompt is submitted to the model. Used by the recall layer to inject relevant traces into the context window. |
| **v1** | The first design of Hippo: bronze/silver/gold medallion pipeline with QMD retrieval and a memory subagent. Deprecated. |
| **v2** | The current design of Hippo: episodic trace capture with context injection for recall. This document describes v2. |
