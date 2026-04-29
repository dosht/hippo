# Section 6: Runtime View

## Scenario 1: Session Start (Capture)

Fires on every new Claude Code session.

```mermaid
sequenceDiagram
    participant User
    participant CC as Claude Code
    participant Hook as SessionStart hook<br/>session_start.py
    participant FS as ~/.hippo/sessions.jsonl

    User->>CC: opens new session (any project)
    CC->>Hook: fires SessionStart event (JSON on stdin)
    Hook->>Hook: resolve project_id from git remote or repo path
    Hook->>Hook: compute thread_id = sha1(project_id + ":" + branch)
    Hook->>FS: append sidecar record (session_id, project_id, thread_id, branch, started_at)
    Hook->>CC: exit 0 (no output to model)
    CC->>User: session ready
```

Key invariant: the hook writes nothing to the session JSONL and produces no model output.
If `session_id` is absent from the event, the hook logs and exits cleanly without writing.

---

## Scenario 2: Nightly Ingest Run

Fires daily at 03:00 via launchd.

```mermaid
sequenceDiagram
    participant Launchd
    participant Ingest as ingest_v2.py
    participant SC as ~/.hippo/sessions.jsonl
    participant SRC as ~/.claude/projects/
    participant BR as ~/.hippo/bronze/
    participant MF as ~/.hippo/manifest.jsonl

    Launchd->>Ingest: spawn (daily 03:00)
    Ingest->>SC: load sidecar index (session_id -> record)
    Ingest->>MF: load manifest index (session_id -> row)
    Ingest->>SRC: discover all *.jsonl files
    loop for each session file
        Ingest->>Ingest: check sidecar gate (skip if no sidecar)
        Ingest->>Ingest: check cutoff gate (skip if before HIPPO_INGEST_FROM)
        Ingest->>Ingest: check staleness (skip if mtime+size unchanged)
        Ingest->>BR: copy session to bronze/<project_id>/<session_id>.jsonl
        Ingest->>MF: append manifest row (status=bronze or stale)
    end
    Ingest->>Ingest: log summary counts
```

---

## Scenario 3: Episode Extraction [QUEUED]

Planned nightly run after ingest, reads bronze sessions and emits traces.

```mermaid
sequenceDiagram
    participant Extractor as episode extractor [QUEUED]
    participant MF as manifest.jsonl
    participant BR as bronze/<project_id>/
    participant Claude as claude -p (Haiku)
    participant TR as traces/<project_id>.jsonl

    Extractor->>MF: find sessions with status=bronze or stale
    loop for each qualifying session
        Extractor->>BR: read session JSONL
        Extractor->>Claude: call with trajectory extraction prompt
        Claude-->>Extractor: JSON array of traces
        Extractor->>TR: append traces
        Extractor->>MF: update status=extracted
        Extractor->>Extractor: check quota (QuotaExhaustedError -> graceful stop)
    end
```

---

## Scenario 4: Recall Injection [QUEUED]

Fires before each user prompt is submitted to the model.

```mermaid
sequenceDiagram
    participant User
    participant CC as Claude Code
    participant Hook as UserPromptSubmit hook [QUEUED]
    participant IDX as ~/.hippo/index/
    participant TR as traces/<project_id>.jsonl

    User->>CC: types prompt
    CC->>Hook: fires UserPromptSubmit event (prompt + cwd)
    Hook->>Hook: extract cues from prompt + git context
    Hook->>IDX: query vector index for top-K traces by cues
    IDX-->>Hook: trace IDs + scores
    Hook->>TR: read trace bodies for top-K IDs
    Hook->>Hook: pack traces into bundle (<= 2000 tokens)
    Hook->>CC: prepend trace bundle to system context
    CC->>CC: submits enriched prompt to model
```

Key invariant: if the index is absent or the query fails, the hook exits cleanly with no
injection. Recall failure must never block prompt submission.
