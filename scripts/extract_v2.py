#!/usr/bin/env python3
"""Hippo episode extractor (v2).

Reads bronze entries with status in (bronze, stale) from
~/.hippo/manifest.jsonl, calls `claude -p` with the delta-extraction
prompt, and writes 0-N trace records to
~/.hippo/traces/<project_id>.jsonl.

Status transitions:
  bronze | stale  --extract-->  extracted (success, 0 or more traces written)
                              | failed    (non-quota, with-stderr error)

On rate-limit / silent-failure, raises QuotaExhaustedError and the run
exits cleanly without marking the session failed; next scheduled run
picks it up.
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
MANIFEST_PATH = HIPPO_HOME / "manifest.jsonl"
TRACES_ROOT = HIPPO_HOME / "traces"
LOG_PATH = HIPPO_HOME / "logs" / "extract.log"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "extract_v2.md"

EXTRACTION_MODEL = os.environ.get("HIPPO_EXTRACT_MODEL", "claude-sonnet-4-5")
EXTRACTION_TIMEOUT_SEC = int(os.environ.get("HIPPO_EXTRACT_TIMEOUT", "180"))


class QuotaExhaustedError(RuntimeError):
    """Raised when claude -p indicates quota/rate-limit exhaustion."""


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}"
    print(line)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    rows: list[dict] = []
    with MANIFEST_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def latest_per_session(rows: list[dict]) -> dict[str, dict]:
    """Merge rows by session_id in order; later fields override earlier.

    This preserves immutable fields (bronze_path, project_id, ...) written
    by ingest while letting later status-update rows mutate status/error/etc.
    """
    out: dict[str, dict] = {}
    for r in rows:
        sid = r.get("session_id")
        if not sid:
            continue
        if sid in out:
            out[sid].update(r)
        else:
            out[sid] = dict(r)
    return out


def append_manifest(row: dict) -> None:
    with MANIFEST_PATH.open("a") as f:
        f.write(json.dumps(row) + "\n")


def append_trace(project_id: str, trace: dict) -> None:
    TRACES_ROOT.mkdir(parents=True, exist_ok=True)
    path = TRACES_ROOT / f"{project_id}.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(trace) + "\n")


def trace_id(project_id: str, body: str, kind: str) -> str:
    h = hashlib.sha1(f"{project_id}:{kind}:{body}".encode()).hexdigest()[:16]
    return f"trace_{h}"


def is_rate_limit(rc: int, stderr: str) -> bool:
    if rc == 429:
        return True
    s = stderr.lower()
    return "429" in s or "rate limit" in s


def is_silent_failure(rc: int, stderr: str) -> bool:
    return rc != 0 and stderr.strip() == ""


def call_claude(prompt: str, bronze_text: str) -> str:
    combined = prompt + "\n\n---\n\n" + bronze_text
    try:
        r = subprocess.run(
            [
                "claude", "-p",
                "--strict-mcp-config",
                "--max-turns", "1",
                "--model", EXTRACTION_MODEL,
            ],
            input=combined,
            capture_output=True, text=True,
            timeout=EXTRACTION_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"claude -p timed out after {EXTRACTION_TIMEOUT_SEC}s") from e

    if r.returncode == 0:
        return r.stdout

    stderr = r.stderr or ""
    if is_rate_limit(r.returncode, stderr):
        raise QuotaExhaustedError(f"rate limit (rc={r.returncode}): {stderr.strip()}")
    if is_silent_failure(r.returncode, stderr):
        raise QuotaExhaustedError(
            f"silent failure (rc={r.returncode}, empty stderr) — assuming quota exhausted"
        )
    raise RuntimeError(f"claude -p failed (rc={r.returncode}): {stderr.strip()}")


def parse_traces(raw: str) -> list[dict]:
    """Parse model output as JSON array of traces. Tolerant to fences."""
    s = raw.strip()
    # Strip ```json ... ``` fences if present.
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[:-3].strip()
    try:
        data = json.loads(s)
    except Exception as e:
        raise RuntimeError(f"could not parse traces JSON: {e}; raw[:300]={raw[:300]!r}")
    if not isinstance(data, list):
        raise RuntimeError(f"expected list, got {type(data).__name__}")
    return data


def validate_trace(t: dict) -> bool:
    if not isinstance(t, dict):
        return False
    if t.get("kind") not in ("negative", "fix", "preference", "directive"):
        return False
    cues = t.get("cues")
    if not isinstance(cues, list) or not all(isinstance(c, str) for c in cues):
        return False
    body = t.get("body")
    if not isinstance(body, str) or not body.strip():
        return False
    return True


def extract_session(row: dict, prompt: str) -> int:
    """Extract one session. Returns the count of traces written. May raise QuotaExhaustedError."""
    bronze_path = Path(row["bronze_path"])
    if not bronze_path.exists():
        raise RuntimeError(f"bronze missing: {bronze_path}")
    bronze_text = bronze_path.read_text(errors="replace")

    raw = call_claude(prompt, bronze_text)
    traces = parse_traces(raw)

    project_id = row.get("project_id") or "no-project"
    written = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for t in traces:
        if not validate_trace(t):
            log(f"  skipping invalid trace: {t!r}")
            continue
        record = {
            "id": trace_id(project_id, t["body"], t["kind"]),
            "project_id": project_id,
            "thread_id": row.get("thread_id"),
            "session_id": row.get("session_id"),
            "kind": t["kind"],
            "cues": t["cues"],
            "body": t["body"],
            "created_at": now,
            "strength": 1.0,
            "last_recalled_at": None,
            "recall_count": 0,
            "hit_count": 0,
        }
        append_trace(project_id, record)
        written += 1
    return written


def main() -> int:
    if not PROMPT_PATH.exists():
        log(f"FATAL: prompt missing at {PROMPT_PATH}")
        return 1
    prompt = PROMPT_PATH.read_text()

    rows = load_manifest()
    latest = latest_per_session(rows)
    eligible = [r for r in latest.values() if r.get("status") in ("bronze", "stale")]

    log(f"==== extract v2 start (eligible={len(eligible)}) ====")

    extracted = 0
    failed = 0
    total_traces = 0

    for i, row in enumerate(eligible, start=1):
        sid = row.get("session_id", "<unknown>")
        log(f"[{i}/{len(eligible)}] extracting {sid[:8]} (project={row.get('project_id')})")
        try:
            n = extract_session(row, prompt)
        except QuotaExhaustedError as e:
            log(f"  quota exhausted: {e}. stopping run; status unchanged.")
            return 0
        except Exception as e:
            log(f"  failed: {e}")
            failed += 1
            append_manifest({
                "session_id": sid,
                "status": "failed",
                "stage": "extract",
                "error": str(e),
                "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            continue

        extracted += 1
        total_traces += n
        log(f"  ok: {n} trace(s) written")
        append_manifest({
            "session_id": sid,
            "status": "extracted",
            "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "traces_count": n,
        })

    log(f"==== extract done: extracted={extracted} failed={failed} traces={total_traces} ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
