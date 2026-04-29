# Hippo Code Review

Scope: `scripts/ingest_v2.py`, `scripts/compact.py`, `scripts/extract.py`,
`scripts/manifest.py`, `scripts/hooks/session_start.py`.

`scripts/ingest.py` and `scripts/reconcile.py` are v1 code slated for sunset per STATUS.md.
They are noted at the end but not reviewed in depth.

---

## Critical findings

### 1. `manifest.py:170` -- `update_manifest` rewrites the entire file (read-modify-write race)

`update_manifest` reads all rows, mutates one, then opens the file in `w` mode and rewrites it.
Any concurrent writer (a second launchd fire, a manual run started while the nightly is still
running) that appends a row between the read and the write will lose that row silently.
The concurrent scenario is unlikely today (ingest-v2 is the only writer and jobs are not
overlapping under normal conditions), but the `StartCalendarInterval` does not prevent two
manual runs from overlapping.

Recommendation: before writing, take a `.lock` file with `fcntl.flock` or write to a
`.tmp` file and `os.replace()` it. At minimum document the assumption in the module docstring.

---

### 2. `compact.py:318-332` -- `claude -p` subprocess has no timeout

`subprocess.run` is called without a `timeout=` argument. A hung or very slow claude
process will block the compaction run indefinitely, holding the launchd slot and
preventing the next scheduled run from making progress.

Recommendation: add `timeout=300` (five minutes per session is generous). Catch
`subprocess.TimeoutExpired`, log it, and either raise `QuotaExhaustedError` to stop
the run gracefully or mark the entry `failed` and continue.

---

### 3. `compact.py:344-347` -- silent non-zero exit classified as `QuotaExhaustedError`

When claude exits non-zero with empty stderr, the code raises `QuotaExhaustedError`,
which stops the entire run and leaves the session as `bronze`. The comment says "assuming
quota exhausted". If the actual cause is a file system or binary issue, every future run
will silently stop at the same session.

The backoff+retry logic in the docstring (lines 303-312) is described but not
implemented -- `call_claude_compact` raises immediately on rate-limit errors without
retrying. The retry loop exists nowhere in the call stack.

Recommendation: implement the backoff loop inside `call_claude_compact` for rate-limit
errors only. Silent failures should be retried once then marked `failed` (not
`QuotaExhaustedError`), so they do not permanently block the run.

---

### 4. `manifest.py:143` -- `append_manifest` is not atomic on NFS/APFS

Appending to a shared JSONL file with `open(path, "a")` produces atomic-enough behavior
on local APFS for single-byte writes, but a multi-kilobyte JSON line can be interleaved
with another process's write on some file systems. In practice today this is a single-writer
concern, but the docstring claims "atomic append" which is misleading.

Recommendation: rename the docstring claim to "sequential append" and add a note that
concurrent callers are not supported.

---

### 5. `ingest_v2.py:178-183` -- stale detection uses `int(st_mtime)` truncation

`int(stat.st_mtime)` truncates fractional seconds. If the session file is modified within
the same second as the last ingest run, the change will be missed and the session will
not be reprocessed. On macOS, HFS+ mtime granularity is 1s; APFS is finer but
`int()` discards that precision.

Recommendation: store `source_mtime` as a float and compare with a small epsilon, or
use a content hash (SHA-1 of the first 1 KB) as a cheap change signal alongside size.

---

### 6. `hooks/session_start.py:104` -- `started_at` uses wall clock, not event timestamp

The sidecar record stores `time.strftime(...)` (hook execution time) as `started_at`.
If the hook fires with a delay (launchd hook queue backup), `started_at` will be later
than the actual session start. The ingest cutoff check (`ingest_v2.py:167`) then compares
this approximate value against the cutoff date.

For sessions that start just before midnight, a delayed hook could push `started_at`
past the cutoff of the following day and cause the session to be skipped.

Recommendation: prefer `event.get("started_at")` from the SessionStart event payload if
present; fall back to wall clock only when the field is absent.

---

### 7. `compact.py:391` -- `_project_slug` regex is fragile and hardcoded to `mu`

The regex `r'^-Users-[^-]+-(?:src|Business|Desktop|Documents)-(.+)$'` assumes the OS
username matches `[^-]+` (one non-hyphen segment) and that the repo lives under one of
four known directories. A repo at `/Users/mu/work/clients/acme/thing` would fall through
and return the raw hash.

The function is used only for display (silver frontmatter). It is low-risk today but will
surprise a second contributor.

Recommendation: remove the regex. Use `Path(project_hash.replace("-", "/"))` to
reconstruct the path then call `.name` (or `.parts[-2:]`). This is simpler and handles
any layout.

---

### 8. `ingest_v2.py:96-99` -- `write_manifest_row` uses v2-specific schema

`ingest_v2.py` has its own inline `write_manifest_row` function that writes a different
set of fields than the schema defined in `manifest.py`. The v2 row omits several fields
(`bronze_size_bytes`, `harness`, `short`, `memory_query`, `meta_path`, `agent`,
`parent_session`) that downstream code (`compact.py`, `extract.py`) expects.

Once milestone 7 (episode extractor) reads `status in (bronze, stale)` rows written
by ingest_v2, missing fields will surface as `None` unexpectedly.

Recommendation: either import and use `append_manifest` from `manifest.py` and write all
schema fields (with explicit `None` for v2-irrelevant ones), or add a note in the v2
manifest schema documenting which fields are absent and what values downstream should
assume.

---

### 9. `compact.py:555-556` -- silver append uses Python `open("a")` without fsync

For the continuation path (parts 2..N), new content is appended with `open(silver_path, "a")`.
There is no `fh.flush()` + `os.fsync()` before the manifest is updated. If the process
is killed between the write and the manifest update, the silver file has new bytes but
the manifest still shows the previous state. Re-running would re-append the same section.

Recommendation: `fh.flush(); os.fsync(fh.fileno())` before closing, then update manifest.

---

## Sunset candidates (v1 code that conflicts with v2 direction)

**`scripts/compact.py` and `scripts/extract.py` (the entire v1 gold pipeline)**

These produce silver summaries and gold markdown entries from a session-summary shape.
STATUS.md milestone 7 replaces this with an episode extractor that emits terse JSONL
traces. The silver layer, gold markdown, QMD index, and `extract.py` will all be
superseded. `compact.py`'s `QuotaExhaustedError` pattern is worth porting (noted in
STATUS.md milestone 11); the rest is scheduled for removal.

Mark both files with a `# SUNSET: replaced by episode extractor (milestone 7)` header
comment so future contributors know not to extend them.

**`scripts/ingest.py`** (v1 ingest)

Superseded by `ingest_v2.py`. The v1 plist (`com.mu.hippo.nightly`) has already been
removed from launchd per STATUS.md. The file should be deleted or archived when v2 has
been running cleanly for one week.

**`scripts/manifest.py`'s `update_manifest`**

The rewrite-in-place approach was adequate for v1. The v2 episode extractor will write
many more rows per run. Consider migrating to an append-only pattern where status updates
are new rows and readers use last-write-wins (same as `ingest_v2.py`'s `load_manifest`).
