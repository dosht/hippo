# Section 8: Crosscutting Concepts

## 1. Episodic Trace Data Model

The trace is the atomic unit of memory in v2. It replaces the gold markdown document from v1.

```jsonc
{
  "id": "<uuid>",
  "type": "negative | fix | preference | directive",
  "cues": ["keyword1", "keyword2", "..."],
  "body": "terse, concrete, agent-readable text",
  "strength": 1.0,
  "thread_id": "<sha1 hex>",
  "project_id": "<sha1 hex>",
  "session_id": "<uuid>",
  "ts": "2026-04-28T03:00:00+00:00",
  "last_used": null
}
```

**Trace types**:
- `negative`: something that went wrong. The failure mode is the memory, not the fix.
- `fix`: a confirmed solution to a recurring problem.
- `preference`: user-stated or inferred preference about how to do something.
- `directive`: an explicit instruction that should persist across sessions ("always use X when Y").

**Design rules for traces**:
- Each trace must stand alone. No cross-references to other traces.
- `body` is concrete: file paths, error messages, command names, port numbers. No abstract heuristics.
- `cues` are the retrieval surface. Short keywords and phrases extracted from context.
- `strength` starts at 1.0. Consolidation modifies it. Prune threshold is configurable (default: 0.1).

Reference: `scripts/extract_episodes.py` [QUEUED] will enforce this shape via a JSON schema
validation step before writing to the trace store.

---

## 2. Project and Thread Identity Rules

Implemented in `scripts/hooks/session_start.py`.

**Project identity**:
```
project_id = sha1(remote_url)            # git repo with remote
project_id = sha1("local:" + repo_root)  # git repo, no remote
project_id = null                        # not a git repo
```

**Thread identity**:
```
thread_id = sha1(project_id + ":" + branch)
thread_id = null   # if project_id is null, branch is null, or HEAD is detached
```

**Invariants**:
- Renaming the remote URL creates a new project_id. Acceptable (rare event).
- Deleting and recreating a branch with the same name produces the same thread_id. Intentional
  per ADR-006: this is the simplest rule that handles the common case correctly.
- Multiple concurrent sessions on the same project+branch share a thread_id. The episode
  extractor must handle interleaved turns when this occurs.

---

## 3. Cutoff and Baseline Strategy

The ingest cutoff is a single date stored in `~/.hippo/config` as `HIPPO_INGEST_FROM=YYYY-MM-DD`.

**Why a hard cutoff rather than backfilling old sessions**: see ADR-007.

**Operational rule**: the cutoff is set once at setup time and never moved backward. Moving
it backward re-admits pre-cutoff sessions, which may include observer/plugin noise from before
the sidecar gate was established.

**Sidecar gate and cutoff work together**:
- Sessions before the cutoff: skipped by date filter, regardless of sidecar.
- Sessions after the cutoff without a sidecar: skipped by sidecar gate.
- Sessions after the cutoff with a sidecar: eligible for ingest.

This means the first session after initial setup is the baseline. All memory is post-baseline.

---

## 4. Quota-Aware Graceful Stop Pattern [QUEUED]

The episode extractor calls `claude -p` once per session. Long nightly runs risk exhausting
the Max subscription daily quota mid-run.

Pattern (to be implemented in `scripts/extract_episodes.py`):
1. Catch `QuotaExhaustedError` (or detect rate-limit signature in `claude -p` stderr).
2. Flush the manifest with the current state (partial run is fine; incomplete sessions remain
   at status `bronze` and are picked up next night).
3. Write a summary log entry: how many extracted, how many deferred.
4. Exit with code 0. Do not retry. The next nightly run resumes automatically.

**Key invariant**: a quota stop must never leave a trace store in a partially-written state
for a single session. Trace writes for one session are atomic: all traces for a session are
written together, then the manifest row is updated. If the process is killed mid-write, the
manifest row stays `bronze` and the session is reprocessed next night (mtime check catches
any partial bronze re-copy; the trace store can accumulate duplicate traces for that session
until a de-dup pass runs).

---

## 5. Sidecar Append-Only Protocol

`~/.hippo/sessions.jsonl` is append-only. Multiple records for the same `session_id` may exist
(hook fires twice on re-entry). Consumers (ingest, extractor) use **last-write-wins** by reading
all lines and keeping the latest record per `session_id`.

No consumer may delete or truncate the sidecar. If it grows too large, archive old entries
manually (date-based cutoff). Do not automate this; losing sidecar records for un-ingested
sessions would cause them to be permanently skipped.

---

## 6. Bronze Immutability Protocol

Once a bronze file is written, it is never modified. If the source session changes (appended
turns, Claude Code writes more content), `ingest_v2.py` detects the mtime/size mismatch and
overwrites the bronze copy, writing a new manifest row with `status=stale`.

`stale` status means: the bronze copy is newer than any extracted traces. The extractor must
re-extract all traces for that session (replacing prior traces from the same session_id in
the trace store).
