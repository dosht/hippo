# Section 10: Quality Requirements

## Quality Scenarios

### Q1: Durability

**Scenario**: Claude Code deletes a session JSONL file after 30 days.
**Required response**: The bronze copy in `~/.hippo/bronze/<project_id>/` already exists.
No data is lost.
**How guaranteed**: `ingest_v2.py` runs nightly. Any session that has a sidecar and passes
the cutoff gate is copied to bronze within 24 hours of starting. The 30-day GC window
provides ample headroom.
**Failure mode**: If the laptop is off for more than 30 days, sessions started during that
window may be GC'd before ingest runs. Acceptable given the single-user setup.

### Q2: Plugin-Noise-Proof

**Scenario**: The `claude-mem` plugin (or any other observer plugin) spawns sessions in
`~/.claude/projects/` that should not enter Hippo's memory.
**Required response**: Those sessions are silently skipped by the sidecar gate.
**How guaranteed**: `ingest_v2.py` requires a matching `session_id` in `~/.hippo/sessions.jsonl`.
The `SessionStart` hook only fires for sessions started by the user directly (via the
hook registration in `~/.claude/settings.json`). Plugin-spawned sessions do not trigger
this hook.
**Failure mode**: If a plugin somehow injects a matching `session_id` into the sidecar, the
session would be ingested. This is considered an extraordinary attack scenario.

### Q3: Branch-Deletion-Proof

**Scenario**: A feature branch is merged, deleted, and recreated with the same name for a
new piece of work.
**Required response**: The new sessions on the recreated branch are assigned the same
`thread_id` as the old sessions. Traces from both coexist in the trace store. The
consolidation decay mechanism gradually reduces the strength of traces from the old work.
**How guaranteed**: `thread_id = sha1(project_id + ":" + branch)` is deterministic and
does not depend on branch lifecycle. See ADR-006.

### Q4: Quota-Graceful

**Scenario**: The episode extractor is mid-run and the Max subscription daily quota is
exhausted.
**Required response**: The extractor detects the quota error, flushes the manifest with
the current state, logs a summary, and exits cleanly. Unprocessed sessions remain at
status `bronze`. The next night's run picks them up automatically.
**How guaranteed**: [QUEUED] The quota-aware graceful stop pattern described in Section 8
must be implemented in `scripts/extract_episodes.py`.
**Current gap**: Not yet implemented. Until milestone 11 lands, a quota exhaustion during
extraction will leave sessions at status `bronze` with a failed run in the log.

### Q5: Agent-Readability

**Scenario**: The UserPromptSubmit hook injects traces before a prompt.
**Required response**: The injected bundle is at most 2000 tokens. Each trace is a single,
terse, concrete statement. The agent can read the bundle without needing to parse structure.
**How guaranteed**: [QUEUED] The recall hook will enforce the token budget by selecting
top-K traces and truncating if the bundle exceeds the cap. The extractor enforces concreteness
by schema validation on the `body` field.
**Current gap**: Both the extractor and the recall hook are not yet implemented.
