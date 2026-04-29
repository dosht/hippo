# Pipeline Conventions

## Overview

The Hippo pipeline is a linear bronze -> silver -> gold progression. Each stage is an
independent Python script that reads `manifest.jsonl`, does work, and writes updated
manifest rows. Scripts are idempotent: re-running them on the same input produces the
same output.

The v1 pipeline (ingest.py, compact.py, extract.py) is being sunset. The v2 pipeline
(ingest_v2.py + future episode extractor) is the active path. See STATUS.md for the
milestone queue.

## Status field semantics

Every manifest row has a `status` field. Valid values:

| Status | Written by | Meaning |
|--------|-----------|---------|
| `bronze` | `ingest_v2.py` | Raw session copied to `~/.hippo/bronze/`. Ready for extraction. |
| `stale` | `ingest_v2.py` | Session was previously ingested but source file changed (mtime/size mismatch). Bronze copy has been refreshed. |
| `extracted` | episode extractor (milestone 7) | Traces written to `~/.hippo/traces/`. |
| `skipped-no-sidecar` | `ingest_v2.py` (counter only) | Session has no sidecar entry; not written to manifest. |
| `skipped-cutoff` | `ingest_v2.py` (counter only) | Session started before `HIPPO_INGEST_FROM`; not written. |
| `failed` | any script | Unrecoverable error. `error` field populated. |

V1-only statuses (`silver`, `gold`, `skipped-large`) appear in existing manifests but
are not produced by v2 scripts.

## Manifest row contract

Every row written to `manifest.jsonl` must include:

- `session_id` (str): unique key, the UUID from the source JSONL filename.
- `status` (str): one of the values above.
- `ingested_at` (str): ISO8601 UTC timestamp of when this row was written.

All other fields are optional but should be included when available. Missing fields must
serialize as `null`, not be omitted, so that downstream scripts can call `.get("field")`
reliably without `KeyError`.

When a script updates an existing row (e.g., status changes from `bronze` to
`extracted`), it must use the safe-rewrite pattern (write to `.tmp` then `os.replace`).
See coding-standards.md.

## Idempotency expectations

Every pipeline stage must be safe to run multiple times on the same input:

- **Ingest**: re-running on an unchanged session file produces no new manifest row
  (mtime + size match). A modified session file writes a new row with `status: stale`.
- **Episode extractor** (milestone 7): before writing traces, check whether a trace
  with the same content hash already exists. Skip if found.
- **Consolidation** (milestone 10): use upsert semantics, not append-only.

## Graceful quota stop pattern

When calling `claude -p` in a long-running loop, the script must stop cleanly when the
API quota is exhausted rather than marking every remaining session as `failed`.

Pattern (from `compact.py`, worth porting to the episode extractor):

```python
class QuotaExhaustedError(RuntimeError):
    """Raised when claude -p indicates quota/rate-limit exhaustion.

    Treated as a graceful stop signal. The in-flight entry stays at its
    current status so the next scheduled run picks it up.
    """

# In the main loop:
try:
    result = call_claude(...)
except QuotaExhaustedError as exc:
    log.warning("Quota exhausted: %s. Stopping run.", exc)
    return 0   # exit 0 so launchd does not flag the job as failed
except Exception as exc:
    log.error("Failed on session %s: %s", session_id, exc)
    update_manifest(session_id, {"status": "failed", "error": str(exc)})
    # continue to next session
```

Classify as `QuotaExhaustedError`:
- HTTP 429 response (returncode 429 from `claude -p`).
- Stderr containing "429" or "rate limit" (case-insensitive).

Do NOT classify as `QuotaExhaustedError`:
- Silent non-zero exit with empty stderr (current `compact.py` behavior is a bug; see
  CODE_REVIEW.md #3). These should retry once, then mark `failed`.

## Subprocess calls to `claude -p`

Always include:
- `timeout=300` (five minutes per session is a safe upper bound for extraction).
- `capture_output=True, text=True, check=False`.
- Log both `returncode` and `stderr` on failure.

```python
result = subprocess.run(
    ["claude", "-p", "--strict-mcp-config", "--max-turns", "1",
     "--model", MODEL, prompt_and_input],
    capture_output=True,
    text=True,
    check=False,
    timeout=300,
)
```

After the call:
1. If `returncode == 0`: use `result.stdout`.
2. If rate-limit error: raise `QuotaExhaustedError`.
3. If timeout: log and either raise `QuotaExhaustedError` (stops the run) or mark
   `failed` (continues). Prefer `failed` for timeouts since the entry is likely
   abnormally large, not a quota issue.
4. Otherwise: raise `RuntimeError` with `returncode` and `stderr`.

## Schedules

### Installed plists

| Label | Plist | Fires |
|-------|-------|-------|
| `com.mu.hippo.ingest-v2` | `scripts/launchd/com.mu.hippo.ingest-v2.plist` | Daily 03:00 |

The v1 plists (`com.mu.hippo.nightly`, `com.mu.hippo.retry`) exist in the repo for
reference but have been removed from launchd. Do not re-install them.

### Plist structure

Each plist must include:
- `Label`: reverse-DNS, `com.mu.hippo.<name>`.
- `ProgramArguments`: absolute path to python3 and the script. No shell expansion.
- `WorkingDirectory`: repo root.
- `EnvironmentVariables`: `PATH`, `HOME`, `HIPPO_HOME` at minimum.
- `StandardOutPath` and `StandardErrorPath`: separate log files under `~/.hippo/logs/`.
- `RunAtLoad`: `<false/>` unless you want the job to fire immediately on boot.
- `StartCalendarInterval`: use `Hour` + `Minute` for daily jobs.

### Install and uninstall

```bash
# Install
bash scripts/launchd/install.sh

# Verify
launchctl list | grep hippo

# Manual smoke test (fires immediately, does not wait for schedule)
launchctl kickstart -k "gui/$UID/com.mu.hippo.ingest-v2"

# Check output
tail -f ~/.hippo/logs/ingest.stdout.log
tail -f ~/.hippo/logs/ingest.stderr.log

# Uninstall
bash scripts/launchd/uninstall.sh
```

`install.sh` is idempotent: it calls `launchctl bootout` before `bootstrap` so
re-running it on an already-loaded job is safe.

### Adding a new scheduled job

1. Copy `scripts/launchd/com.mu.hippo.ingest-v2.plist` to a new file with a new label.
2. Update `ProgramArguments`, `StandardOutPath`, `StandardErrorPath`, and
   `StartCalendarInterval`.
3. Add the new label to the `for label in ...` loop in both `install.sh` and
   `uninstall.sh`.
4. Update `README.md` in this directory to list the new job.

### Log paths

```
~/.hippo/logs/ingest.stdout.log     # combined print() output from ingest_v2.py
~/.hippo/logs/ingest.stderr.log     # Python tracebacks and uncaught exceptions
~/.hippo/logs/ingest.log            # structured log written by log() helper
~/.hippo/logs/session_start.log     # hook log (one line per session)
```

A non-empty `ingest.stderr.log` after a nightly run means the script crashed. Check it
first when diagnosing a missed run.
