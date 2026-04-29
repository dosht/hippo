# Claude Code Hooks

## What hooks are

Claude Code hooks are shell commands registered in `settings.json` that fire at specific
points in the agent lifecycle. Hippo uses them to capture sidecar metadata without any
manual action from the user.

## Registration

Claude Code looks for hook registrations in (in order):
1. `~/.claude/settings.json` (user-level, all projects)
2. `<project>/.claude/settings.json` (project-level)
3. `<project>/.claude/settings.local.json` (local overrides, gitignored)

The SessionStart hook must be registered at the user level so it fires in every project,
not just in the Hippo repo. Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/YOU/src/hippo-public/scripts/hooks/session_start.py"
          }
        ]
      }
    ]
  }
}
```

Replace `/Users/YOU/src/hippo-public` with your actual repo path. The path must be
absolute because the hook runs in an unspecified cwd.

To verify the hook is registered:

```bash
grep -A5 "SessionStart" ~/.claude/settings.json
```

To verify it fires: open a new Claude Code session, then check:

```bash
tail -5 ~/.hippo/sessions.jsonl
tail -5 ~/.hippo/logs/session_start.log
```

## Constraints for SessionStart hooks

The `SessionStart` event fires before the model produces any output. The hook's stdout
is NOT injected into the model's context. This means:

- Side effects (writing files, logging) are fine.
- Producing output that the model reads requires a different hook type (`UserPromptSubmit`
  or `PreToolUse`). Do not use `SessionStart` for recall injection (that is milestone 9).
- The hook must exit within a few seconds. Long-running commands will delay session
  startup for the user.
- Do not call `claude` or any other model from within a `SessionStart` hook. It creates
  a recursive session and wastes quota.

## The canonical example: `scripts/hooks/session_start.py`

This script is the reference implementation. It demonstrates:

1. **Reading the event from stdin** (`json.load(sys.stdin)`). The event is a JSON object
   with at minimum `session_id` and `cwd`. Check for `sys.stdin.isatty()` before reading
   so the script is safe to run manually for testing.

2. **Git identity resolution** using short-lived subprocesses with `timeout=2`. Each
   subprocess call is wrapped in a helper (`sh()`) that catches all exceptions and
   returns `None` on any failure. The hook must never crash even if git is unavailable.

3. **Deterministic IDs** using `sha1()` truncated to 16 hex characters. The truncation
   is intentional -- the full SHA-1 is unnecessary and the short form is easier to read
   in logs.

4. **Append-only write** to `~/.hippo/sessions.jsonl`. The file is never read by the
   hook. Last-write-wins deduplication happens at ingest time.

5. **Logging to a file**, not stdout. `LOG_PATH` is separate from `SIDECAR_PATH`. The
   hook's stdout is discarded by Claude Code; writing to stdout is wasted.

## Writing a new hook

1. Create the script under `scripts/hooks/<hook_name>.py`.
2. Accept the event from stdin (use the `sh()` + `json.load(sys.stdin)` pattern from
   `session_start.py`).
3. Write side effects to a file under `HIPPO_HOME` (default `~/.hippo/`).
4. Never write to stdout with the intent of influencing the model (for SessionStart).
5. Exit 0 on all handled paths. Log failures to the hook's own log file.
6. Test manually:
   ```bash
   echo '{"session_id":"test-abc","cwd":"/tmp"}' | python3 scripts/hooks/session_start.py
   tail -5 ~/.hippo/sessions.jsonl
   ```
7. Register in `~/.claude/settings.json` and open a new session to verify the sidecar
   row appears.

## Known limitations

- Multiple firings per session: if Claude Code restarts within the same session, the
  hook may fire twice with the same `session_id`. The sidecar receives two rows; ingest
  uses last-write-wins, which is correct behavior.
- No hook for session end: there is no `SessionEnd` event in Claude Code today. The
  ingest script detects "session is no longer growing" via mtime + size comparison.
- Hook execution order is not guaranteed when multiple hooks share a type.
