# Coding Standards

## Python

### Version and tooling

- **Minimum Python version:** 3.11. All scripts use `from __future__ import annotations`
  for forward-reference compatibility. New code should target 3.12+ features only when
  there is a concrete reason; keep the floor at 3.11 until the launchd plist is updated.
- **Formatter:** Black with default settings (line length 88). Run before committing.
- **Linter:** ruff. Configuration lives in `pyproject.toml` when present; otherwise use
  ruff defaults. The minimum rule set is `E,F,I` (pycodestyle errors, pyflakes, isort).
- **Type hints:** required on all public functions. Use built-in generics (`list[str]`,
  `dict[str, dict]`) not `typing.List`/`typing.Dict`. Use `X | None` not `Optional[X]`.
  Return types are mandatory.

Why: the pipeline scripts run unattended at 3 AM. Type hints and ruff catch the classes
of error (missing field access, wrong argument type) that produce silent manifest
corruption or dropped sessions.

### Docstrings

Use Google-style docstrings for public functions with non-obvious behavior. One-line
docstrings are fine for simple helpers. Include `Args:`, `Returns:`, and `Raises:`
sections when the function can raise a named exception callers must handle.

```python
def load_sidecar(path: Path) -> dict[str, dict]:
    """Return {session_id: latest_record} from a sessions.jsonl file.

    Last-write-wins when multiple rows share a session_id.

    Args:
        path: Absolute path to the sidecar JSONL file.

    Returns:
        Empty dict if the file does not exist.
    """
```

### Error handling

- Never swallow exceptions silently. At minimum log the exception text.
- Use specific exception types in `except` clauses. Reserve bare `except Exception`
  for top-level catch-all blocks that log and continue (as in the ingest loop).
- Distinguish recoverable (log + continue) from fatal (log + return non-zero) errors.
- For subprocess calls to `claude -p`: always supply `timeout=`. See pipeline.md for
  the recommended value and error classification table.

### File I/O

- Use `pathlib.Path` throughout. Never `os.path.join`.
- Prefer `path.read_text(encoding="utf-8")` and `path.write_text(...)` for whole-file
  operations.
- For append-only JSONL files (`sessions.jsonl`, `manifest.jsonl`): open in `"a"` mode
  and write one complete JSON line terminated by `\n` per call.
- For rewrite operations (e.g., `manifest.py:update_manifest`): write to a `.tmp` file
  first, then `os.replace(tmp, final)`. This prevents a half-written manifest if the
  process is killed mid-write. Current code does NOT do this (see CODE_REVIEW.md #1);
  new code must.

Example of the safe rewrite pattern:

```python
import os, tempfile
from pathlib import Path

def safe_write_jsonl(path: Path, rows: list[dict]) -> None:
    dir_ = path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=path.name + ".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise
```

### Path handling and the gold symlink

`gold/entries/` in the repo is gitignored. On a working machine it is either a real
directory or a symlink to `HIPPO_GOLD_DIR`. Always resolve paths before using them in
subprocess arguments or os.stat calls:

```python
effective_path = Path(os.environ.get("HIPPO_GOLD_DIR", "gold/entries")).resolve()
```

Do not assume `Path.resolve()` follows symlinks uniformly; test with `Path.is_dir()`
after resolving.

### Constants

Define module-level constants in `UPPER_SNAKE_CASE` at the top of the file, after
imports, with a comment explaining the value's meaning and where to update it if it
changes. Do not embed magic numbers inline.

## Bash / shell scripts

- Shebang: `#!/usr/bin/env bash`.
- Always start with `set -euo pipefail`.
- Quote all variable expansions: `"$VAR"`, `"${VAR:-default}"`.
- Use `[[ ]]` for conditionals, not `[ ]`.
- Use `local` for all variables inside functions.
- Print to stderr for error messages: `echo "ERROR: ..." >&2`.
- Avoid here-strings for multi-line content; write to a temp file instead.
- launchd install scripts: print the `launchctl kickstart` smoke-test command at the end.

## Commit messages

Format: `<scope>: <imperative verb> <what>`

Scopes: `ingest`, `compact`, `extract`, `hooks`, `manifest`, `launchd`, `docs`, `chore`.

Examples:
- `ingest: skip sessions without sidecar`
- `hooks: record hook_version in sidecar`
- `manifest: use atomic rewrite for update_manifest`
- `docs: add pipeline.md coding standards`

Breaking changes: append `!` after scope and add a `BREAKING CHANGE:` footer.

PRs: title mirrors the commit message of the primary change. Body lists motivation,
what changed, and any manual steps required (e.g., "run `launchctl unload` and
re-run `install.sh`").
