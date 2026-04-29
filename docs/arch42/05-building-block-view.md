# Section 5: Building Block View

Three layers: **Capture**, **Consolidation**, and **Recall**.

```
┌─────────────────────────────────────────────────────────────────┐
│ CAPTURE                                                         │
│  SessionStart hook  ──►  sidecar (sessions.jsonl)               │
│  ingest_v2.py       ──►  bronze/<project_id>/<session_id>.jsonl │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ CONSOLIDATION  [mostly QUEUED]                                  │
│  episode extractor  ──►  traces/<project_id>.jsonl              │
│  consolidation pass ──►  strengthen / decay / prune traces      │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ RECALL  [QUEUED]                                                │
│  UserPromptSubmit hook  ──►  inject top-K traces into context   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Capture

### SessionStart Hook

**Entry point**: `scripts/hooks/session_start.py`
**Status**: DONE

Fires on every Claude Code session start. Resolves project identity and thread identity from
the working directory's git state. Appends a single JSON record to `~/.hippo/sessions.jsonl`.
Does not touch the session JSONL. Side-effects only, no stdout output to the model.

Key logic:
- `project_id = sha1(remote_url)` or `sha1("local:" + repo_realpath)` if no remote
- `thread_id = sha1(project_id + ":" + branch)`, null if detached HEAD or no git
- Duplicate hook fires (re-entry) produce multiple sidecar rows; ingest deduplicates by
  last-write-wins on `session_id`

Registered in `~/.claude/settings.json` under `hooks.SessionStart`.

### Ingest v2

**Entry point**: `scripts/ingest_v2.py`
**Status**: DONE

Nightly batch job. Walks `~/.claude/projects/`, applies three filters in order:
1. **Sidecar gate**: session must have a record in `~/.hippo/sessions.jsonl`
2. **Cutoff gate**: session `started_at` must be >= `HIPPO_INGEST_FROM` from `~/.hippo/config`
3. **Staleness check**: skip if bronze copy exists and `source_mtime` + `source_size` match manifest

Copies qualifying sessions to `~/.hippo/bronze/<project_id>/<session_id>.jsonl`. Writes a
manifest row to `~/.hippo/manifest.jsonl` with status `bronze` (new) or `stale` (reprocessed).

Scheduled via: `scripts/launchd/com.mu.hippo.ingest-v2.plist` (daily at 03:00).

---

## Layer 2: Consolidation

### Shared Error Module

**Entry point**: `scripts/errors.py`
**Status**: DONE

Defines `QuotaExhaustedError` and the detection helpers `is_rate_limit_error` /
`is_transient_silent_failure`. All pipeline scripts that call `claude -p` import from here.
See Section 8.4 for the Quota-Aware Graceful Stop pattern.

### Episode Extractor [QUEUED]

**Planned entry point**: `scripts/extract_episodes.py` (not yet implemented)
**Status**: QUEUED (milestone 7)

Reads sessions with manifest status `bronze` or `stale`. For each session, calls `claude -p`
(Haiku model) with a trajectory prompt to extract episodic traces. Each trace is a JSON object:

```
{
  "type": "negative|fix|preference|directive",
  "cues": [...],
  "body": "...",
  "strength": 1.0,
  "thread_id": "...",
  "project_id": "...",
  "session_id": "...",
  "ts": "..."
}
```

Appends traces to `~/.hippo/traces/<project_id>.jsonl`. Updates manifest status to `extracted`.

Must implement the quota-aware graceful stop pattern (see Section 8).

### Trace Store + Vector Index [QUEUED]

**Planned path**: `~/.hippo/traces/<project_id>.jsonl` + `~/.hippo/index/`
**Status**: QUEUED (milestone 8)

Flat JSONL files per project. Vector index (faiss or QMD) built over `cues + body` fields.
Supports semantic nearest-neighbour lookup at recall time.

### Consolidation Pass [QUEUED]

**Planned entry point**: `scripts/consolidate.py` (not yet implemented)
**Status**: QUEUED (milestone 10)

Runs nightly after extraction. Applies three operations to the trace store:
- **Strengthen**: increment `strength` on traces whose `cues` overlapped with a recent session's
  content (usage signal)
- **Decay**: multiply `strength` by a decay factor for all traces not recently used
- **Prune**: delete traces whose `strength` drops below a threshold

---

## Layer 3: Recall

### UserPromptSubmit Hook [QUEUED]

**Planned entry point**: `scripts/hooks/user_prompt_submit.py` (not yet implemented)
**Status**: QUEUED (milestone 9)

Fires before each user prompt is submitted to the model. Extracts cues from the current prompt
and working directory context. Queries the vector index for top-K traces. Injects the trace
bundle as a `system` context block prepended to the prompt.

Recall budget: 2000 tokens maximum for the injected trace bundle (configurable in `~/.hippo/config`).

Registered in `~/.claude/settings.json` under `hooks.UserPromptSubmit`.
