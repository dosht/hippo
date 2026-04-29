#!/usr/bin/env python3
"""Hippo SessionStart hook.

Records sidecar metadata for each Claude Code session: project identity,
branch, thread, started_at. Writes to ~/.hippo/sessions.jsonl.

Triggered by Claude Code SessionStart hook. Receives event JSON on stdin.
Side-effects only — does not produce output for the model.

Project identity rules:
- git remote → project_id = sha1(remote_url)
- git no-remote → project_id = sha1("local:" + repo_realpath)
- no git → project_id = null

Thread rule:
- thread_id = sha1(project_id + ":" + branch)
- null project_id or null branch → null thread_id
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HIPPO_HOME = Path(os.environ.get("HIPPO_HOME", os.path.expanduser("~/.hippo")))
SIDECAR_PATH = HIPPO_HOME / "sessions.jsonl"
LOG_PATH = HIPPO_HOME / "logs" / "session_start.log"


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}\n")


def sh(args: list[str], cwd: str | None = None) -> str | None:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=2)
        if r.returncode != 0:
            return None
        return r.stdout.strip() or None
    except Exception:
        return None


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def resolve_project(cwd: str) -> tuple[str | None, str | None, str | None]:
    """Return (project_id, repo_root, branch) for the given cwd.

    project_id is null when cwd is not a git repo.
    branch is null on detached HEAD (returns "DETACHED@<sha>" instead).
    """
    repo_root = sh(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    if repo_root is None:
        return None, None, None

    remote = sh(["git", "config", "--get", "remote.origin.url"], cwd=cwd)
    if remote:
        project_id = sha1(remote)
    else:
        project_id = sha1("local:" + os.path.realpath(repo_root))

    branch = sh(["git", "symbolic-ref", "--short", "HEAD"], cwd=cwd)
    if branch is None:
        short_sha = sh(["git", "rev-parse", "--short", "HEAD"], cwd=cwd) or "unknown"
        branch = f"DETACHED@{short_sha}"

    return project_id, repo_root, branch


def thread_id_for(project_id: str | None, branch: str | None) -> str | None:
    if not project_id or not branch or branch.startswith("DETACHED@"):
        return None
    return sha1(f"{project_id}:{branch}")


def main() -> int:
    try:
        event = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except Exception as e:
        log(f"failed to parse event: {e}")
        event = {}

    session_id = event.get("session_id")
    cwd = event.get("cwd") or os.getcwd()
    source = event.get("source", "unknown")

    if not session_id:
        log(f"no session_id in event; skipping. event_keys={list(event.keys())}")
        return 0

    project_id, repo_root, branch = resolve_project(cwd)
    thread_id = thread_id_for(project_id, branch)

    record = {
        "session_id": session_id,
        "started_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "cwd": cwd,
        "repo_root": repo_root,
        "project_id": project_id,
        "branch": branch,
        "thread_id": thread_id,
        "source": source,
        "hook_version": 1,
    }

    SIDECAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SIDECAR_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")

    log(f"recorded session={session_id[:8]} project={project_id} branch={branch} thread={thread_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
