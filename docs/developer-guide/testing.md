# Testing

## Current state

There is no automated test suite today. This is intentional for the early pipeline: the
primary risk is incorrect manifest state transitions and missed sessions, both of which
are caught by reading the manifest and logs after a real nightly run.

## Recommended minimum (not yet implemented)

The following is the pragmatic floor that should exist before milestone 7 (episode
extractor) ships. Mark tasks here as done when implemented.

### 1. Unit tests for `manifest.py`

`scripts/manifest.py` is pure Python with no external dependencies. It is the most
critical module to test because a bug there can corrupt all pipeline state.

Test cases to cover:
- `read_manifest`: empty file, non-existent file, malformed JSON line (must skip, not crash).
- `append_manifest`: verify line is appended with correct newline.
- `update_manifest`: field merge, `KeyError` on missing session_id.
- Round-trip: append then read, update then read.

Recommended tool: `pytest`. No special fixtures needed; use `tmp_path` (built-in pytest
fixture) for temp file paths.

```python
# tests/test_manifest.py
import json
from pathlib import Path
from scripts.manifest import read_manifest, append_manifest, update_manifest

def test_read_empty(tmp_path):
    assert read_manifest(str(tmp_path / "m.jsonl")) == []

def test_round_trip(tmp_path):
    path = str(tmp_path / "m.jsonl")
    append_manifest(path, {"session_id": "abc", "status": "bronze"})
    rows = read_manifest(path)
    assert len(rows) == 1
    assert rows[0]["session_id"] == "abc"
```

### 2. Smoke tests for `hooks/session_start.py`

A smoke test exercises the hook's main code path without a real Claude Code session.

```bash
# Run from repo root
echo '{"session_id":"smoke-test-001","cwd":"/tmp"}' \
  | python3 scripts/hooks/session_start.py

# Verify output
python3 -c "
import json, sys
rows = [json.loads(l) for l in open('$HOME/.hippo/sessions.jsonl') if l.strip()]
hit = [r for r in rows if r['session_id'] == 'smoke-test-001']
assert hit, 'smoke test row not found'
print('OK:', hit[-1])
"
```

Edge cases to exercise manually:
- Empty stdin (no event): hook must exit 0 without writing.
- Event with no `session_id`: hook must log and exit 0.
- `cwd` pointing to a non-git directory: hook must write a row with `project_id: null`.

### 3. Dry-run mode for pipeline scripts

`ingest_v2.py` does not yet have a `--dry-run` flag. Add one: it should print what would
be ingested without copying files or writing manifest rows. This lets you validate the
cutoff logic and sidecar matching against a real `~/.claude/projects/` without side effects.

`compact.py` already has `--dry-run`. The episode extractor (milestone 7) must include
it from the start.

### 4. Sample data fixtures

Store a minimal synthetic bronze session file at `tests/fixtures/sample_session.jsonl`
(two JSONL lines: one `summary` record and one `assistant` record). This allows unit
tests for the extractor to run without real session data.

The sidecar record for the fixture should live alongside it at
`tests/fixtures/sample_sidecar.jsonl`.

## What NOT to test

- LLM output quality. Silver and trace quality are evaluated through human review and
  the manual end-to-end run (STATUS.md verification steps), not automated assertions.
- `claude -p` integration. Do not mock the subprocess; test the code that wraps it
  (error classification, backoff logic) by injecting a fake callable.
- The QMD index. It is a derived artifact rebuilt from gold files. Test that gold files
  are written with correct frontmatter; do not test search results.

## Running tests (once implemented)

```bash
# Install dev dependencies (recommended)
pip install pytest pytest-cov

# Run all tests
pytest tests/

# Run with coverage
pytest --cov=scripts tests/
```

No CI is wired up today. Run tests locally before merging to main.
