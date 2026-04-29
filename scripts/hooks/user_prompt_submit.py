#!/usr/bin/env python3
"""Hippo recall hook (UserPromptSubmit).

For each user prompt, identify the current project (from cwd's git
remote, falling back to repo realpath, falling back to no-project),
load all traces for that project_id, score by keyword overlap with the
prompt, and emit a context block listing the top-K traces.

Claude Code reads stdout from UserPromptSubmit hooks as additional
context for the model.

Quiet on missing data — if no traces match, prints nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HIPPO_HOME = Path(os.environ.get("HIPPO_HOME", os.path.expanduser("~/.hippo")))
TRACES_ROOT = HIPPO_HOME / "traces"
RECALL_LOG = HIPPO_HOME / "logs" / "recall.log"
SIDECAR_PATH = HIPPO_HOME / "sessions.jsonl"

TOP_K = int(os.environ.get("HIPPO_RECALL_TOP_K", "5"))
MIN_SCORE = float(os.environ.get("HIPPO_RECALL_MIN_SCORE", "1.0"))


def log(msg: str) -> None:
    RECALL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RECALL_LOG.open("a") as f:
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


def resolve_project_id(cwd: str) -> str | None:
    repo_root = sh(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    if repo_root is None:
        return None
    remote = sh(["git", "config", "--get", "remote.origin.url"], cwd=cwd)
    if remote:
        return sha1(remote)
    return sha1("local:" + os.path.realpath(repo_root))


def load_traces(project_id: str | None) -> list[dict]:
    out: list[dict] = []
    if not TRACES_ROOT.exists():
        return out
    paths = []
    if project_id:
        p = TRACES_ROOT / f"{project_id}.jsonl"
        if p.exists():
            paths.append(p)
    # Always include the global / no-project file if present.
    for name in ("no-project.jsonl", "_global.jsonl"):
        p = TRACES_ROOT / name
        if p.exists():
            paths.append(p)
    seen: dict[str, dict] = {}
    for p in paths:
        with p.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                tid = rec.get("id")
                if not tid:
                    out.append(rec)
                    continue
                # Last write wins so recall-state updates supersede the original.
                seen[tid] = rec
    out.extend(seen.values())
    return out


_TOKEN_RE = re.compile(r"[a-z0-9_/.\-]+", re.IGNORECASE)

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "but", "not",
    "are", "was", "were", "have", "has", "had", "you", "your", "our", "they",
    "their", "them", "what", "when", "where", "which", "who", "why", "how",
    "all", "any", "some", "one", "two", "can", "could", "would", "should",
    "will", "may", "might", "must", "let", "lets", "now", "then", "there",
    "here", "out", "off", "over", "under", "about", "also", "just", "than",
    "too", "very", "much", "more", "less", "most", "least", "today", "tomorrow",
    "yesterday", "look", "looking", "see", "want", "need", "needs", "use",
    "used", "using", "make", "makes", "made", "get", "gets", "got",
}


def tokenize(s: str) -> set[str]:
    toks = (t.lower() for t in _TOKEN_RE.findall(s))
    return {t for t in toks if len(t) >= 3 and t not in _STOPWORDS}


def score(trace: dict, prompt_tokens: set[str]) -> float:
    """Keyword overlap score, weighted: cue match counts more than body."""
    cue_tokens: set[str] = set()
    for c in trace.get("cues", []):
        cue_tokens |= tokenize(c)
    body_tokens = tokenize(trace.get("body", ""))
    cue_overlap = len(cue_tokens & prompt_tokens)
    body_overlap = len(body_tokens & prompt_tokens)
    base = 2.0 * cue_overlap + 1.0 * body_overlap
    strength = float(trace.get("strength", 1.0))
    return base * strength


def render_block(traces: list[dict]) -> str:
    """Format selected traces as a system-context block."""
    lines = ["<hippo-memory>"]
    lines.append("These are episodic memory traces from past sessions in this project. They are deltas from past surprises (corrections, fixes, preferences). Apply them silently when relevant; do not cite them.")
    for t in traces:
        kind = t.get("kind", "?")
        body = t.get("body", "").strip()
        lines.append(f"- [{kind}] {body}")
    lines.append("</hippo-memory>")
    return "\n".join(lines)


def update_recall_state(traces: list[dict]) -> None:
    """Bump recall_count and last_recalled_at on selected traces.

    Append a single replacement line per trace (last-merge-wins applies on
    next read), so we never rewrite the source file in place.
    """
    if not traces:
        return
    by_project: dict[str, list[dict]] = {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for t in traces:
        t["recall_count"] = int(t.get("recall_count", 0)) + 1
        t["last_recalled_at"] = now
        pid = t.get("project_id") or "no-project"
        by_project.setdefault(pid, []).append(t)
    for pid, ts in by_project.items():
        path = TRACES_ROOT / f"{pid}.jsonl"
        with path.open("a") as f:
            for t in ts:
                f.write(json.dumps(t) + "\n")


def main() -> int:
    try:
        event = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except Exception as e:
        log(f"failed to parse event: {e}")
        return 0

    prompt = event.get("prompt", "") or ""
    cwd = event.get("cwd") or os.getcwd()

    if not prompt.strip():
        return 0

    project_id = resolve_project_id(cwd)
    traces = load_traces(project_id)
    if not traces:
        log(f"no traces for project={project_id}")
        return 0

    prompt_tokens = tokenize(prompt)
    scored = [(score(t, prompt_tokens), t) for t in traces]
    scored.sort(key=lambda p: p[0], reverse=True)
    selected = [t for s, t in scored if s >= MIN_SCORE][:TOP_K]

    if not selected:
        log(f"no traces above min_score={MIN_SCORE} for project={project_id}")
        return 0

    block = render_block(selected)
    print(block)
    update_recall_state(selected)
    log(f"injected {len(selected)} trace(s) for project={project_id} prompt_chars={len(prompt)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
