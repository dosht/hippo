#!/usr/bin/env python3
"""Hippo ingest v2.

Walks ~/.claude/projects/, picks up sessions that:
  1. Started on or after HIPPO_INGEST_FROM (cutoff date).
  2. Have a sidecar record in ~/.hippo/sessions.jsonl (skip plugin-spawned
     and pre-hook sessions).
  3. Are new OR have been modified since their last bronze copy
     (mtime/size mismatch → reprocess from scratch).

Writes bronze copies to ~/.hippo/bronze/<project_id>/<session_id>.jsonl
and a manifest row to ~/.hippo/manifest.jsonl.

Status values: bronze | stale | extracted | skipped-no-sidecar | skipped-cutoff
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

HIPPO_HOME = Path(os.environ.get("HIPPO_HOME", os.path.expanduser("~/.hippo")))
SIDECAR_PATH = HIPPO_HOME / "sessions.jsonl"
MANIFEST_PATH = HIPPO_HOME / "manifest.jsonl"
BRONZE_ROOT = HIPPO_HOME / "bronze"
LOG_PATH = HIPPO_HOME / "logs" / "ingest.log"
CONFIG_PATH = HIPPO_HOME / "config"

CLAUDE_PROJECTS_ROOT = Path(os.path.expanduser("~/.claude/projects"))


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}"
    print(line)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def read_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if CONFIG_PATH.exists():
        for line in CONFIG_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def load_sidecar() -> dict[str, dict]:
    """Return {session_id: latest_record}. Last write wins."""
    out: dict[str, dict] = {}
    if not SIDECAR_PATH.exists():
        return out
    with SIDECAR_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            sid = rec.get("session_id")
            if sid:
                out[sid] = rec
    return out


def load_manifest() -> dict[str, dict]:
    """Return {session_id: row}. Last write wins."""
    out: dict[str, dict] = {}
    if not MANIFEST_PATH.exists():
        return out
    with MANIFEST_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            sid = rec.get("session_id")
            if sid:
                out[sid] = rec
    return out


def write_manifest_row(row: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("a") as f:
        f.write(json.dumps(row) + "\n")


def session_started_at(jsonl_path: Path) -> str | None:
    """Read the first line; pull the timestamp if present."""
    try:
        with jsonl_path.open() as f:
            first = f.readline()
        if not first:
            return None
        rec = json.loads(first)
        return rec.get("timestamp") or rec.get("ts")
    except Exception:
        return None


def parse_iso_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        # Accept either YYYY-MM-DD or full ISO
        if len(s) == 10:
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def discover_sessions() -> list[Path]:
    if not CLAUDE_PROJECTS_ROOT.exists():
        return []
    return list(CLAUDE_PROJECTS_ROOT.rglob("*.jsonl"))


def main() -> int:
    cfg = read_config()
    cutoff = parse_iso_date(cfg.get("HIPPO_INGEST_FROM", ""))
    if cutoff is None:
        log("WARN: no HIPPO_INGEST_FROM in config; using epoch 0")
        cutoff = datetime.fromtimestamp(0, tz=timezone.utc)

    log(f"==== ingest v2 start (cutoff={cutoff.isoformat()}) ====")

    sidecar = load_sidecar()
    manifest = load_manifest()
    log(f"sidecar entries: {len(sidecar)} | manifest entries: {len(manifest)}")

    sessions = discover_sessions()
    log(f"discovered {len(sessions)} session JSONL files")

    counts = {"new": 0, "stale": 0, "unchanged": 0, "no-sidecar": 0, "cutoff": 0, "error": 0}

    for src in sessions:
        session_id = src.stem
        # Strip Claude Code's _agent-* and _part_* suffixes if present;
        # session_id matches the UUID prefix only.
        # Sidecar uses the raw session_id from Claude Code, which is the UUID.
        # If our filename is `<uuid>_agent-xxx.jsonl`, the sidecar id is just <uuid>.
        # Match permissively: try exact, then prefix.
        sc = sidecar.get(session_id)
        if sc is None and "_" in session_id:
            sc = sidecar.get(session_id.split("_", 1)[0])

        if sc is None:
            counts["no-sidecar"] += 1
            continue

        started = parse_iso_date(session_started_at(src) or sc.get("started_at", ""))
        if started is None or started < cutoff:
            counts["cutoff"] += 1
            continue

        try:
            stat = src.stat()
        except Exception as e:
            log(f"stat failed for {src}: {e}")
            counts["error"] += 1
            continue

        prior = manifest.get(session_id)
        is_unchanged = (
            prior is not None
            and prior.get("source_size") == stat.st_size
            and prior.get("source_mtime") == int(stat.st_mtime)
        )
        if is_unchanged:
            counts["unchanged"] += 1
            continue

        project_id = sc.get("project_id") or "no-project"
        bronze_dir = BRONZE_ROOT / project_id
        bronze_dir.mkdir(parents=True, exist_ok=True)
        bronze_path = bronze_dir / f"{session_id}.jsonl"

        try:
            shutil.copy2(src, bronze_path)
        except Exception as e:
            log(f"copy failed {src} -> {bronze_path}: {e}")
            counts["error"] += 1
            continue

        is_stale = prior is not None
        row = {
            "session_id": session_id,
            "source_path": str(src),
            "source_size": stat.st_size,
            "source_mtime": int(stat.st_mtime),
            "bronze_path": str(bronze_path),
            "project_id": project_id,
            "branch": sc.get("branch"),
            "thread_id": sc.get("thread_id"),
            "cwd": sc.get("cwd"),
            "started_at": started.isoformat(),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "status": "stale" if is_stale else "bronze",
        }
        write_manifest_row(row)

        if is_stale:
            counts["stale"] += 1
        else:
            counts["new"] += 1

    log(f"==== ingest done: {counts} ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
