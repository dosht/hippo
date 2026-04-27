"""
scripts/ingest.py -- Bronze layer ingestion.

Scans ~/.claude/projects/ for new JSONL session files and copies them to
bronze/, updating manifest.jsonl. Sessions already present in the manifest
(matched by session_id derived from filename) are skipped to ensure
idempotency.

Also scans subagent sessions at:
  <project>/<parent-session-uuid>/subagents/agent-*.jsonl

Each subagent becomes its own manifest entry with session_id derived from
the agent JSONL filename (e.g. agent-a7b3a2427ff7af6b7), parent_session set
to the outer session UUID, and agent/agent_task populated from the sibling
.meta.json file.

Usage:
  python scripts/ingest.py [--dry-run] [--manifest PATH] [--source DIR]
                           [--bronze-dir DIR]

Wave 1 / Story S7. Flesh out this stub; do NOT modify manifest.py signatures.
Wave 4 / Story S15: Extended to ingest subagent sessions.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.manifest import append_manifest, find_entry, read_manifest

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("ingest")


# ---------------------------------------------------------------------------
# Constants (tune in implementation, do not change schema fields)
# ---------------------------------------------------------------------------

# Sessions exceeding this byte count are marked skipped-large and not copied.
LARGE_SESSION_BYTES_THRESHOLD = 10_000_000

# Sessions exceeding this byte count are split into parts at user-turn
# boundaries (see _split_jsonl_user_aligned). Soft target leaves headroom for
# the compact prompt; hard cap forces a cut even mid-turn.
CHUNK_SOFT_BYTES = 250_000
CHUNK_HARD_BYTES = 350_000

# Sessions with bronze_size_bytes below this are flagged short: true.
SHORT_SESSION_BYTES_THRESHOLD = 10_000

# Default locations (resolved relative to project root at runtime)
DEFAULT_SOURCE_DIR = Path.home() / ".claude" / "projects"
DEFAULT_BRONZE_DIR = Path(__file__).parent.parent / "bronze"
DEFAULT_MANIFEST = Path(__file__).parent.parent / "manifest.jsonl"

# Harness tag written to every manifest entry produced by this script.
HARNESS = "claude-code"


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for ingest.py."""
    parser = argparse.ArgumentParser(
        prog="ingest.py",
        description=(
            "Scan ~/.claude/projects/ for new JSONL session files, copy them "
            "to bronze/, and append entries to manifest.jsonl."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be copied without writing anything.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        metavar="DIR",
        help="Source directory to scan for JSONL session files. "
        f"Default: {DEFAULT_SOURCE_DIR}",
    )
    parser.add_argument(
        "--bronze-dir",
        type=Path,
        default=DEFAULT_BRONZE_DIR,
        metavar="DIR",
        help=f"Destination directory for bronze copies. Default: {DEFAULT_BRONZE_DIR}",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        metavar="PATH",
        help=f"Path to manifest.jsonl. Default: {DEFAULT_MANIFEST}",
    )
    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def derive_session_id(jsonl_path: Path) -> str:
    """Derive the session_id from the source JSONL filename.

    Strips the .jsonl extension from the filename. For example,
    abc123.jsonl becomes "abc123".

    Args:
        jsonl_path: Path to the source JSONL file.

    Returns:
        A string used as the unique key in manifest.jsonl.
    """
    return jsonl_path.stem


def extract_agent_and_parent(jsonl_path: Path) -> tuple[str | None, str | None]:
    """Extract agent name and parent session from JSONL session metadata.

    Reads early lines of the file looking for a record with type "agent-name".
    Stops after reading at most 20 lines to keep this fast on large files.

    Args:
        jsonl_path: Path to the source JSONL file.

    Returns:
        A tuple (agent_name, parent_session_id). Either may be None if the
        corresponding metadata is absent. agent_name is the raw agentName
        string from the record. parent_session_id is always None for now
        because Claude Code does not currently embed parentSessionId in JSONL.
    """
    agent_name: str | None = None
    parent_session: str | None = None

    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 20:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "agent-name":
                    agent_name = record.get("agentName") or None
                if "parentSessionId" in record:
                    parent_session = record["parentSessionId"] or None
    except OSError as exc:
        log.warning("Could not read %s for metadata extraction: %s", jsonl_path, exc)

    return agent_name, parent_session


def extract_session_metadata(jsonl_path: Path) -> dict:
    """Extract session-level provenance metadata from a JSONL session file.

    Reads up to the first 20 non-permission-mode records and returns the first
    seen values of cwd, gitBranch, and timestamp. Uses the same forgiving JSON
    parsing as extract_agent_and_parent.

    Records with type "permission-mode" are skipped when searching for
    timestamp and cwd/gitBranch so that the bootstrap record does not
    shadow meaningful session activity.

    Args:
        jsonl_path: Path to the source JSONL file.

    Returns:
        A dict with keys:
          cwd (str or None): Exact working directory from the first record
              that carries it.
          git_branch (str or None): Git branch from the first record that
              carries the gitBranch field.
          session_started_at (str or None): ISO8601 timestamp from the first
              record that carries a timestamp field (permission-mode excluded).
    """
    cwd: str | None = None
    git_branch: str | None = None
    session_started_at: str | None = None

    seen_non_permission = 0

    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if seen_non_permission >= 20:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # Malformed line: count it against the budget so we don't
                    # scan unboundedly on a corrupt file, but keep going.
                    seen_non_permission += 1
                    continue

                is_permission_mode = record.get("type") == "permission-mode"
                if not is_permission_mode:
                    seen_non_permission += 1

                # cwd and gitBranch may appear on permission-mode records too;
                # capture from any record to maximise coverage.
                if cwd is None and record.get("cwd"):
                    cwd = str(record["cwd"])
                if git_branch is None and record.get("gitBranch"):
                    git_branch = str(record["gitBranch"])

                # timestamp: skip permission-mode records so we capture the
                # true session start time, not the harness bootstrap time.
                if session_started_at is None and not is_permission_mode:
                    ts = record.get("timestamp")
                    if ts:
                        session_started_at = str(ts)

                # Stop early once all three fields are found.
                if (
                    cwd is not None
                    and git_branch is not None
                    and session_started_at is not None
                ):
                    break

    except OSError as exc:
        log.warning(
            "Could not read %s for session metadata extraction: %s", jsonl_path, exc
        )

    return {
        "cwd": cwd,
        "git_branch": git_branch,
        "session_started_at": session_started_at,
        "project": _project_from_cwd(cwd),
    }


_GIT_URL_RE = re.compile(r"[/:]([^/:]+?)(?:\.git)?$")


def _project_from_cwd(cwd: str | None) -> str | None:
    """Resolve a stable project name from a session's cwd.

    Strategy: parse the basename of the `origin` remote URL — the most
    reliable signal of the project's identity, independent of local layout
    (worktrees, branch-named subdirs, custom checkout locations all share
    one origin). Falls back to the parent of `git --git-common-dir` for
    repos with no remote, then None if it isn't a git repo at all (callers
    fall back to the path-decoded project_hash slug).
    """
    if not cwd:
        return None
    cwd_path = Path(cwd)
    if not cwd_path.exists():
        return None

    try:
        url = subprocess.check_output(
            ["git", "-C", str(cwd_path), "remote", "get-url", "origin"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        m = _GIT_URL_RE.search(url)
        if m:
            return m.group(1)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    try:
        common = subprocess.check_output(
            ["git", "-C", str(cwd_path), "rev-parse", "--git-common-dir"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        if common:
            common_path = Path(common)
            if not common_path.is_absolute():
                common_path = (cwd_path / common_path).resolve()
            return common_path.parent.name or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return None


def count_messages(jsonl_path: Path) -> int:
    """Count user and assistant message lines in a JSONL session file.

    Not used by ingest (which uses byte count for the short flag), but
    retained as a utility for future pipeline stages.

    Args:
        jsonl_path: Path to the source JSONL file.

    Returns:
        Number of user/assistant message lines parsed from the file.
    """
    count = 0
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") in ("user", "assistant"):
                    count += 1
    except OSError as exc:
        log.warning("Could not count messages in %s: %s", jsonl_path, exc)
    return count


def _bronze_filename(session_id: str, source_path: Path) -> str:
    """Derive the bronze filename from the source file's modification time.

    Uses the source file's mtime date (YYYY-MM-DD) as the date prefix so
    the bronze name reflects when the session was last active.

    Args:
        session_id: The unique session identifier.
        source_path: The source JSONL path (used for mtime).

    Returns:
        Filename string: YYYY-MM-DD_<session-id>.jsonl
    """
    try:
        mtime = source_path.stat().st_mtime
        date_str = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")
    except OSError:
        date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return f"{date_str}_{session_id}.jsonl"


def read_subagent_meta(meta_path: Path) -> tuple[str | None, str | None]:
    """Read agent type and task description from a subagent .meta.json file.

    The meta file is a sibling of the subagent JSONL in the subagents/ directory.
    Expected format: {"agentType": "tech-lead", "description": "<task summary>"}

    Tolerates missing or malformed meta files -- returns (None, None) gracefully.

    Args:
        meta_path: Path to the .meta.json file (may not exist).

    Returns:
        Tuple (agent_type, agent_task). Either may be None if the file is
        absent, malformed, or the field is missing. agent_task is truncated
        to 500 characters to keep manifest lines bounded.
    """
    if not meta_path.exists():
        return None, None
    try:
        raw = meta_path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read subagent meta %s: %s", meta_path, exc)
        return None, None
    if not isinstance(data, dict):
        log.warning("Unexpected meta format in %s (not a dict)", meta_path)
        return None, None
    agent_type = data.get("agentType") or None
    description = data.get("description") or None
    if description is not None:
        description = description[:500]
    return agent_type, description


def _subagent_bronze_filename(
    agent_id: str,
    parent_session: str,
    source_path: Path,
    session_started_at: str | None = None,
) -> str:
    """Derive the bronze filename for a subagent session.

    Format: YYYY-MM-DD_<parent-session>_<agent-id>.jsonl

    The date comes from session_started_at (MVP-1-14 field) when available,
    falling back to the file's mtime.

    Args:
        agent_id: The subagent identifier (e.g. agent-a7b3a2427ff7af6b7).
        parent_session: The parent session UUID (outer session directory name).
        source_path: The source JSONL path (used for mtime fallback).
        session_started_at: ISO8601 timestamp from session records (MVP-1-14).
            When provided this is used as the date source; mtime is the fallback.

    Returns:
        Filename string: YYYY-MM-DD_<parent-session>_<agent-id>.jsonl
    """
    date_str: str | None = None
    if session_started_at:
        try:
            date_str = session_started_at[:10]  # take YYYY-MM-DD prefix
        except (TypeError, IndexError):
            date_str = None
    if not date_str:
        # Fallback to mtime (session_started_at not yet extracted or missing).
        try:
            mtime = source_path.stat().st_mtime
            date_str = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
                "%Y-%m-%d"
            )
        except OSError:
            date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return f"{date_str}_{parent_session}_{agent_id}.jsonl"


def _is_user_turn_line(line_bytes: bytes) -> bool:
    """Return True if a JSONL line represents a top-level user message.

    User messages are the cleanest "episode boundary" in the RL framing of a
    session — each one represents a new instruction or course correction.
    Cutting on user-turn boundaries preserves the (state, action, reward,
    adjustment) arc within each chunk.

    A record is treated as a user-turn boundary only when type == "user" and
    the message role is "user" (excludes synthesised tool-result wrappers
    that may share type=="user" but carry role=="tool").
    """
    try:
        record = json.loads(line_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if record.get("type") != "user":
        return False
    msg = record.get("message")
    if isinstance(msg, dict):
        role = msg.get("role")
        if role and role != "user":
            return False
    return True


def _split_jsonl_user_aligned(
    jsonl_path: Path,
    soft_bytes: int = CHUNK_SOFT_BYTES,
    hard_bytes: int = CHUNK_HARD_BYTES,
) -> list[list[bytes]]:
    """Split a JSONL session file into byte-similar, user-turn-aligned chunks.

    Aim for ~`target_parts` chunks of roughly equal byte size. "Similar size,
    not exact": each chunk targets ``total_size / target_parts`` bytes
    (uniform per-part target) and cuts at the next user-turn boundary once
    that target is reached. If no user-turn appears for a long stretch the
    chunk grows past target rather than cutting mid-turn — accepted trade
    for tool-call-coherent boundaries (the assistant has yielded back to
    the user at every user-turn line, so any open tool-call has already
    produced its tool-result).

    Splitting is always line-aligned. Concatenating all chunks reproduces
    the original file byte-for-byte. Bytes are a close proxy for tokens at
    Claude's tokenizer ratio, which is what compact's context budget cares
    about.

    Args:
        jsonl_path: Source session JSONL file.
        soft_bytes: Used to compute ``target_parts = ceil(total / soft)``.
            The actual per-part target is ``total / target_parts``.
        hard_bytes: Kept for signature compatibility; unused.

    Returns:
        A list of chunks; each chunk is a list of raw line bytes.
    """
    del hard_bytes  # unused

    with open(jsonl_path, "rb") as fh:
        lines = fh.readlines()
    if not lines:
        return []

    total_size = sum(len(line) for line in lines)
    if total_size <= soft_bytes:
        return [lines]

    target_parts = (total_size + soft_bytes - 1) // soft_bytes
    target_part_bytes = total_size // target_parts

    chunks: list[list[bytes]] = []
    current: list[bytes] = []
    current_size = 0

    for i, line in enumerate(lines):
        current.append(line)
        current_size += len(line)

        # Stop cutting once we've produced target_parts - 1 chunks; the
        # remainder forms the final chunk regardless of size.
        if len(chunks) + 1 >= target_parts:
            continue

        if current_size >= target_part_bytes:
            next_line = lines[i + 1] if i + 1 < len(lines) else None
            if next_line is not None and _is_user_turn_line(next_line.strip() or b"{}"):
                chunks.append(current)
                current = []
                current_size = 0

    if current:
        chunks.append(current)

    return chunks


def _write_part(part_path: Path, lines: list[bytes]) -> int:
    """Write a list of raw JSONL line bytes to a part file. Returns size."""
    part_path.parent.mkdir(parents=True, exist_ok=True)
    with open(part_path, "wb") as fh:
        for line in lines:
            fh.write(line)
    return part_path.stat().st_size


def _harness_bronze_dir(bronze_dir: Path, harness: str) -> Path:
    """Return the harness-namespaced bronze subdirectory.

    All per-session writes go into ``bronze_dir/<harness>/`` so that future
    sources (Codex, Hermes, etc.) can coexist without filename collisions.
    The parent ``bronze_dir`` itself remains the DEFAULT_BRONZE_DIR root.

    Args:
        bronze_dir: The top-level bronze root directory.
        harness: The harness tag (e.g. ``"claude-code"``).

    Returns:
        Path to ``bronze_dir/<harness>/``. Not created here; callers must
        call ``mkdir(parents=True, exist_ok=True)`` before writing.
    """
    return bronze_dir / harness


def _ingest_chunked_top_level(
    jsonl_path: Path,
    session_id: str,
    project_hash: str,
    bronze_dir: Path,
    manifest_path: Path,
    agent_name: str | None,
    parent_session: str | None,
    memory_query: bool,
    session_meta: dict,
    now_iso: str,
    dry_run: bool,
) -> bool:
    """Split a large top-level session into parts and append per-part manifest entries.

    Each part is written as ``YYYY-MM-DD_<sid>_part_<NN>.jsonl`` under
    ``bronze_dir/<HARNESS>/``. Each part has its own manifest entry with
    ``session_id = "<sid>_part_<NN>"`` and ``part_index`` / ``total_parts``
    populated. ``parent_session`` for chunked top-level sessions points to
    the original session_id (the logical parent of the parts).

    Idempotency: if the original session was previously chunked, manifest
    entries with session_id ``<sid>_part_*`` already exist and the caller
    short-circuits via ``find_entry``. (Caller-side guard added separately.)
    """
    chunks = _split_jsonl_user_aligned(jsonl_path)
    total_parts = len(chunks)
    harness_bronze_dir = _harness_bronze_dir(bronze_dir, HARNESS)
    base_filename = _bronze_filename(session_id, jsonl_path)
    base_stem = base_filename.removesuffix(".jsonl")

    log.info(
        "CHUNK %s: splitting %d bytes into %d parts (soft=%d, hard=%d)",
        session_id,
        jsonl_path.stat().st_size,
        total_parts,
        CHUNK_SOFT_BYTES,
        CHUNK_HARD_BYTES,
    )

    if dry_run:
        for i, lines in enumerate(chunks, start=1):
            part_size = sum(len(b) for b in lines)
            log.info(
                "DRY-RUN would write part %d/%d for %s (size=%d)",
                i,
                total_parts,
                session_id,
                part_size,
            )
        return True

    for i, lines in enumerate(chunks, start=1):
        part_idx = i
        part_filename = f"{base_stem}_part_{part_idx:02d}.jsonl"
        part_path = harness_bronze_dir / part_filename
        part_size = _write_part(part_path, lines)
        part_session_id = f"{session_id}_part_{part_idx:02d}"
        entry = {
            "session_id": part_session_id,
            "source_path": str(jsonl_path.resolve()),
            "bronze_path": str(part_path.resolve()),
            "meta_path": None,
            "silver_path": None,
            "gold_paths": [],
            "status": "bronze",
            "ingested_at": now_iso,
            "compacted_at": None,
            "extracted_at": None,
            "bronze_size_bytes": part_size,
            "silver_size_bytes": None,
            "error": None,
            "harness": HARNESS,
            "project_hash": project_hash,
            "agent": agent_name,
            "agent_task": None,
            # parent_session: for chunked top-level sessions points to the
            # original session_id; subagents already use this field for the
            # outer-session UUID, so the meaning is "logical parent of this
            # entry" in either case.
            "parent_session": parent_session if parent_session else session_id,
            "short": part_size < SHORT_SESSION_BYTES_THRESHOLD,
            "memory_query": memory_query,
            "cwd": session_meta["cwd"],
            "project": session_meta.get("project"),
            "git_branch": session_meta["git_branch"],
            "session_started_at": session_meta["session_started_at"],
            "part_index": part_idx,
            "total_parts": total_parts,
        }
        append_manifest(str(manifest_path), entry)
        log.info(
            "Ingested chunk %s -> %s (size=%d, part=%d/%d)",
            part_session_id,
            part_filename,
            part_size,
            part_idx,
            total_parts,
        )

    return True


def _ingest_chunked_subagent(
    jsonl_path: Path,
    session_id: str,
    parent_session: str,
    project_hash: str,
    bronze_dir: Path,
    manifest_path: Path,
    agent_name: str | None,
    agent_task: str | None,
    memory_query: bool,
    short: bool,  # unused per-part (each part recomputes); kept for signature symmetry
    session_meta: dict,
    has_meta: bool,
    source_meta_path: Path,
    now_iso: str,
    dry_run: bool,
) -> bool:
    """Split a large subagent session into parts. Mirrors top-level chunking.

    Part filename: ``YYYY-MM-DD_<parent>_<agent-id>_part_<NN>.jsonl``.
    Part session_id: ``<agent-id>_part_<NN>``.
    ``parent_session`` keeps its existing meaning (outer session UUID).
    The sibling .meta.json is copied once alongside part 1 only — all parts
    in the manifest share the same meta_path so downstream readers can find
    the subagent's task description from any part entry.
    """
    chunks = _split_jsonl_user_aligned(jsonl_path)
    total_parts = len(chunks)
    harness_bronze_dir = _harness_bronze_dir(bronze_dir, HARNESS)
    base_filename = _subagent_bronze_filename(
        session_id,
        parent_session,
        jsonl_path,
        session_started_at=session_meta["session_started_at"],
    )
    base_stem = base_filename.removesuffix(".jsonl")

    log.info(
        "CHUNK subagent %s: splitting %d bytes into %d parts",
        session_id,
        jsonl_path.stat().st_size,
        total_parts,
    )

    if dry_run:
        for i, lines in enumerate(chunks, start=1):
            part_size = sum(len(b) for b in lines)
            log.info(
                "DRY-RUN would write subagent part %d/%d for %s (size=%d)",
                i,
                total_parts,
                session_id,
                part_size,
            )
        return True

    # Copy meta once (next to part 01) for self-containment.
    resolved_meta_path: str | None = None
    if has_meta:
        try:
            harness_bronze_dir.mkdir(parents=True, exist_ok=True)
            bronze_meta_path = harness_bronze_dir / f"{base_stem}.meta.json"
            shutil.copy2(str(source_meta_path), str(bronze_meta_path))
            resolved_meta_path = str(bronze_meta_path.resolve())
        except OSError as exc:
            log.warning(
                "Failed to copy meta for chunked subagent %s: %s", session_id, exc
            )

    for i, lines in enumerate(chunks, start=1):
        part_idx = i
        part_filename = f"{base_stem}_part_{part_idx:02d}.jsonl"
        part_path = harness_bronze_dir / part_filename
        part_size = _write_part(part_path, lines)
        part_session_id = f"{session_id}_part_{part_idx:02d}"
        entry = {
            "session_id": part_session_id,
            "source_path": str(jsonl_path.resolve()),
            "bronze_path": str(part_path.resolve()),
            "meta_path": resolved_meta_path,
            "silver_path": None,
            "gold_paths": [],
            "status": "bronze",
            "ingested_at": now_iso,
            "compacted_at": None,
            "extracted_at": None,
            "bronze_size_bytes": part_size,
            "silver_size_bytes": None,
            "error": None,
            "harness": HARNESS,
            "project_hash": project_hash,
            "agent": agent_name,
            "agent_task": agent_task,
            "parent_session": parent_session,
            "short": part_size < SHORT_SESSION_BYTES_THRESHOLD,
            "memory_query": memory_query,
            "cwd": session_meta["cwd"],
            "project": session_meta.get("project"),
            "git_branch": session_meta["git_branch"],
            "session_started_at": session_meta["session_started_at"],
            "part_index": part_idx,
            "total_parts": total_parts,
        }
        append_manifest(str(manifest_path), entry)
        log.info(
            "Ingested subagent chunk %s -> %s (size=%d, part=%d/%d)",
            part_session_id,
            part_filename,
            part_size,
            part_idx,
            total_parts,
        )

    return True


def ingest_subagent_session(
    jsonl_path: Path,
    bronze_dir: Path,
    manifest_path: Path,
    manifest: list[dict],
    dry_run: bool,
) -> bool:
    """Copy a single subagent session to bronze/ and append a manifest entry.

    Reads the sibling .meta.json to populate agent and agent_task fields, and
    also copies the .meta.json file itself into bronze alongside the JSONL so
    that the bronze layer is self-contained (bronze self-containment principle).

    All bronze writes go into ``bronze/<harness>/`` (harness-namespaced layout).

    Skips the session if its session_id already exists in the manifest.
    Sets status to "skipped-large" if file size exceeds LARGE_SESSION_BYTES_THRESHOLD.
    The session_id is the subagent JSONL stem (e.g. agent-a7b3a2427ff7af6b7).
    parent_session is derived from the grandparent directory (the outer session UUID).

    Args:
        jsonl_path: Absolute path to the subagent JSONL file.
        bronze_dir: Top-level destination directory for bronze copies (the
            harness subdir is derived internally as ``bronze_dir/HARNESS/``).
        manifest_path: Path to manifest.jsonl.
        manifest: Current in-memory manifest (to check for existing entries).
        dry_run: If True, log actions without writing anything.

    Returns:
        True if the session was processed (ingested or skipped-large),
        False if it was already in the manifest and skipped.
    """
    session_id = derive_session_id(jsonl_path)
    # subagents/ dir -> parent is the outer session UUID dir
    parent_session = jsonl_path.parent.parent.name
    # grandparent of the subagent file is the outer session dir,
    # great-grandparent is the project dir
    project_hash = jsonl_path.parent.parent.parent.name

    # Skip sessions already in manifest (idempotency).
    if find_entry(manifest, session_id) is not None:
        log.debug("Skipping subagent %s: already in manifest.", session_id)
        return False
    # Also short-circuit if this subagent was previously ingested as parts.
    if find_entry(manifest, f"{session_id}_part_01") is not None:
        log.debug("Skipping subagent %s: already ingested as parts.", session_id)
        return False

    # Read sibling .meta.json for agent type and task description.
    source_meta_path = jsonl_path.with_suffix("").with_suffix(".meta.json")
    agent_name, agent_task = read_subagent_meta(source_meta_path)
    has_meta = source_meta_path.exists()
    memory_query = bool(agent_name and "memory" in agent_name.lower())

    try:
        source_size = jsonl_path.stat().st_size
    except OSError as exc:
        log.error("Cannot stat %s: %s -- skipping.", jsonl_path, exc)
        return False

    # Extract MVP-1-14 metadata (cwd, git_branch, session_started_at).
    session_meta = extract_session_metadata(jsonl_path)

    bronze_filename = _subagent_bronze_filename(
        session_id,
        parent_session,
        jsonl_path,
        session_started_at=session_meta["session_started_at"],
    )
    # Harness-namespaced bronze directory (bronze/<harness>/).
    harness_bronze_dir = _harness_bronze_dir(bronze_dir, HARNESS)
    bronze_path = harness_bronze_dir / bronze_filename
    # .meta.json gets the same date-prefix stem with .meta.json extension.
    bronze_meta_stem = bronze_filename.removesuffix(".jsonl")
    bronze_meta_path = harness_bronze_dir / f"{bronze_meta_stem}.meta.json"
    short = source_size < SHORT_SESSION_BYTES_THRESHOLD

    now_iso = datetime.now(tz=timezone.utc).isoformat()

    if source_size > LARGE_SESSION_BYTES_THRESHOLD:
        log.info(
            "SKIP-LARGE subagent %s (parent=%s, %d bytes > %d threshold)",
            session_id,
            parent_session,
            source_size,
            LARGE_SESSION_BYTES_THRESHOLD,
        )
        if not dry_run:
            entry: dict = {
                "session_id": session_id,
                "source_path": str(jsonl_path.resolve()),
                "bronze_path": str(bronze_path.resolve()),
                "meta_path": None,
                "silver_path": None,
                "gold_paths": [],
                "status": "skipped-large",
                "ingested_at": now_iso,
                "compacted_at": None,
                "extracted_at": None,
                "bronze_size_bytes": source_size,
                "silver_size_bytes": None,
                "error": None,
                "harness": HARNESS,
                "project_hash": project_hash,
                "agent": agent_name,
                "agent_task": agent_task,
                "parent_session": parent_session,
                "short": short,
                "memory_query": memory_query,
                "cwd": session_meta["cwd"],
                "project": session_meta.get("project"),
                "git_branch": session_meta["git_branch"],
                "session_started_at": session_meta["session_started_at"],
                "part_index": None,
                "total_parts": None,
            }
            append_manifest(str(manifest_path), entry)
        return True

    # Chunk subagent sessions over the soft cap on user-turn boundaries.
    if source_size > CHUNK_SOFT_BYTES:
        return _ingest_chunked_subagent(
            jsonl_path=jsonl_path,
            session_id=session_id,
            parent_session=parent_session,
            project_hash=project_hash,
            bronze_dir=bronze_dir,
            manifest_path=manifest_path,
            agent_name=agent_name,
            agent_task=agent_task,
            memory_query=memory_query,
            short=short,
            session_meta=session_meta,
            has_meta=has_meta,
            source_meta_path=source_meta_path,
            now_iso=now_iso,
            dry_run=dry_run,
        )

    if dry_run:
        log.info(
            "DRY-RUN would ingest subagent: %s -> %s (parent=%s, size=%d, agent=%s, "
            "agent_task=%s, has_meta=%s)",
            jsonl_path,
            bronze_path,
            parent_session,
            source_size,
            agent_name,
            (agent_task[:60] + "...")
            if agent_task and len(agent_task) > 60
            else agent_task,
            has_meta,
        )
        if has_meta:
            log.info(
                "DRY-RUN would copy meta: %s -> %s", source_meta_path, bronze_meta_path
            )
        return True

    try:
        harness_bronze_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(jsonl_path), str(bronze_path))
    except OSError as exc:
        log.error("Failed to copy subagent %s to %s: %s", jsonl_path, bronze_path, exc)
        return False

    # Copy the sibling .meta.json into bronze for self-containment.
    # If the source has no .meta.json, meta_path stays None.
    resolved_meta_path: str | None = None
    if has_meta:
        try:
            shutil.copy2(str(source_meta_path), str(bronze_meta_path))
            resolved_meta_path = str(bronze_meta_path.resolve())
            log.info("Copied meta: %s -> %s", source_meta_path, bronze_meta_path)
        except OSError as exc:
            log.warning(
                "Failed to copy meta %s to %s: %s -- continuing without meta copy.",
                source_meta_path,
                bronze_meta_path,
                exc,
            )

    bronze_size = bronze_path.stat().st_size

    entry = {
        "session_id": session_id,
        "source_path": str(jsonl_path.resolve()),
        "bronze_path": str(bronze_path.resolve()),
        "meta_path": resolved_meta_path,
        "silver_path": None,
        "gold_paths": [],
        "status": "bronze",
        "ingested_at": now_iso,
        "compacted_at": None,
        "extracted_at": None,
        "bronze_size_bytes": bronze_size,
        "silver_size_bytes": None,
        "error": None,
        "harness": HARNESS,
        "project_hash": project_hash,
        "agent": agent_name,
        "agent_task": agent_task,
        "parent_session": parent_session,
        "short": short,
        "memory_query": memory_query,
        "cwd": session_meta["cwd"],
        "project": session_meta.get("project"),
        "git_branch": session_meta["git_branch"],
        "session_started_at": session_meta["session_started_at"],
        "part_index": None,
        "total_parts": None,
    }
    append_manifest(str(manifest_path), entry)

    log.info(
        "Ingested subagent %s -> %s (parent=%s, size=%d, agent=%s, meta_path=%s)",
        session_id,
        bronze_filename,
        parent_session,
        bronze_size,
        agent_name,
        resolved_meta_path,
    )
    return True


def ingest_session(
    jsonl_path: Path,
    bronze_dir: Path,
    manifest_path: Path,
    manifest: list[dict],
    dry_run: bool,
) -> bool:
    """Copy a single session to bronze/ and append a manifest entry.

    All bronze writes go into ``bronze/<harness>/`` (harness-namespaced layout).
    Top-level sessions always have ``meta_path: null`` -- only subagent sessions
    carry a .meta.json sidecar.

    Skips the session if its session_id already exists in the manifest.
    Sets status to "skipped-large" if file size exceeds LARGE_SESSION_BYTES_THRESHOLD.
    Flags short: true if file size is below SHORT_SESSION_BYTES_THRESHOLD.
    Flags memory_query: true if agent name contains "memory".

    Args:
        jsonl_path: Absolute path to the source JSONL file.
        bronze_dir: Top-level destination directory for bronze copies (the
            harness subdir is derived internally as ``bronze_dir/HARNESS/``).
        manifest_path: Path to manifest.jsonl.
        manifest: Current in-memory manifest (to check for existing entries).
        dry_run: If True, log actions without writing anything.

    Returns:
        True if the session was processed (ingested or skipped-large),
        False if it was already in the manifest and skipped.
    """
    session_id = derive_session_id(jsonl_path)
    project_hash = jsonl_path.parent.name

    # Skip sessions already in manifest (idempotency).
    if find_entry(manifest, session_id) is not None:
        log.debug("Skipping %s: already in manifest.", session_id)
        return False
    # Also short-circuit if this source was previously ingested as parts.
    if find_entry(manifest, f"{session_id}_part_01") is not None:
        log.debug("Skipping %s: already ingested as parts.", session_id)
        return False

    try:
        source_size = jsonl_path.stat().st_size
    except OSError as exc:
        log.error("Cannot stat %s: %s -- skipping.", jsonl_path, exc)
        return False

    bronze_filename = _bronze_filename(session_id, jsonl_path)
    # Harness-namespaced bronze directory (bronze/<harness>/).
    harness_bronze_dir = _harness_bronze_dir(bronze_dir, HARNESS)
    bronze_path = harness_bronze_dir / bronze_filename

    now_iso = datetime.now(tz=timezone.utc).isoformat()

    # Determine if this session is too large to ingest.
    if source_size > LARGE_SESSION_BYTES_THRESHOLD:
        log.info(
            "SKIP-LARGE %s (%d bytes > %d threshold)",
            session_id,
            source_size,
            LARGE_SESSION_BYTES_THRESHOLD,
        )
        if not dry_run:
            session_meta = extract_session_metadata(jsonl_path)
            entry: dict = {
                "session_id": session_id,
                "source_path": str(jsonl_path.resolve()),
                "bronze_path": str(bronze_path.resolve()),
                "meta_path": None,
                "silver_path": None,
                "gold_paths": [],
                "status": "skipped-large",
                "ingested_at": now_iso,
                "compacted_at": None,
                "extracted_at": None,
                "bronze_size_bytes": source_size,
                "silver_size_bytes": None,
                "error": None,
                "harness": HARNESS,
                "project_hash": project_hash,
                "agent": None,
                "agent_task": None,
                "parent_session": None,
                "short": source_size < SHORT_SESSION_BYTES_THRESHOLD,
                "memory_query": False,
                "cwd": session_meta["cwd"],
                "project": session_meta.get("project"),
                "git_branch": session_meta["git_branch"],
                "session_started_at": session_meta["session_started_at"],
                "part_index": None,
                "total_parts": None,
            }
            append_manifest(str(manifest_path), entry)
        return True

    # Extract metadata from the session file.
    agent_name, parent_session = extract_agent_and_parent(jsonl_path)
    session_meta = extract_session_metadata(jsonl_path)
    memory_query = bool(agent_name and "memory" in agent_name.lower())
    short = source_size < SHORT_SESSION_BYTES_THRESHOLD

    # Chunk top-level sessions over the soft cap on user-turn boundaries.
    if source_size > CHUNK_SOFT_BYTES:
        return _ingest_chunked_top_level(
            jsonl_path=jsonl_path,
            session_id=session_id,
            project_hash=project_hash,
            bronze_dir=bronze_dir,
            manifest_path=manifest_path,
            agent_name=agent_name,
            parent_session=parent_session,
            memory_query=memory_query,
            session_meta=session_meta,
            now_iso=now_iso,
            dry_run=dry_run,
        )

    if dry_run:
        log.info(
            "DRY-RUN would ingest: %s -> %s (size=%d, agent=%s, short=%s, "
            "memory_query=%s, cwd=%s, git_branch=%s, session_started_at=%s)",
            jsonl_path,
            bronze_path,
            source_size,
            agent_name,
            short,
            memory_query,
            session_meta["cwd"],
            session_meta["git_branch"],
            session_meta["session_started_at"],
        )
        return True

    # Copy the file to bronze/<harness>/.
    try:
        harness_bronze_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(jsonl_path), str(bronze_path))
    except OSError as exc:
        log.error("Failed to copy %s to %s: %s", jsonl_path, bronze_path, exc)
        return False

    bronze_size = bronze_path.stat().st_size

    entry = {
        "session_id": session_id,
        "source_path": str(jsonl_path.resolve()),
        "bronze_path": str(bronze_path.resolve()),
        "meta_path": None,  # top-level sessions never have a .meta.json sidecar
        "silver_path": None,
        "gold_paths": [],
        "status": "bronze",
        "ingested_at": now_iso,
        "compacted_at": None,
        "extracted_at": None,
        "bronze_size_bytes": bronze_size,
        "silver_size_bytes": None,
        "error": None,
        "harness": HARNESS,
        "project_hash": project_hash,
        "agent": agent_name,
        "agent_task": None,
        "parent_session": parent_session,
        "short": short,
        "memory_query": memory_query,
        "cwd": session_meta["cwd"],
        "project": session_meta.get("project"),
        "git_branch": session_meta["git_branch"],
        "session_started_at": session_meta["session_started_at"],
        "part_index": None,
        "total_parts": None,
    }
    append_manifest(str(manifest_path), entry)

    log.info(
        "Ingested %s -> %s (size=%d, agent=%s, short=%s, memory_query=%s, "
        "cwd=%s, git_branch=%s, session_started_at=%s)",
        session_id,
        bronze_filename,
        bronze_size,
        agent_name,
        short,
        memory_query,
        session_meta["cwd"],
        session_meta["git_branch"],
        session_meta["session_started_at"],
    )
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run ingestion. Returns exit code (0 success, non-zero on error)."""
    parser = build_parser()
    args = parser.parse_args(argv)

    source_dir: Path = args.source.expanduser().resolve()
    bronze_dir: Path = args.bronze_dir.expanduser().resolve()
    manifest_path: Path = args.manifest.expanduser().resolve()
    dry_run: bool = args.dry_run

    if not source_dir.is_dir():
        log.error("Source directory does not exist: %s", source_dir)
        return 1

    # Load manifest once; pass it into each ingest_session call so we avoid
    # re-reading from disk on every session.
    manifest = read_manifest(str(manifest_path))
    known_ids: set[str] = {e["session_id"] for e in manifest}

    # Scan all *.jsonl files one level deep in source_dir (each subdirectory
    # is a project hash directory containing session files).
    #
    # Auto-detect whether source_dir is:
    #   (a) the ~/.claude/projects/ root (iterate its subdirs as project dirs), or
    #   (b) a specific project dir (contains .jsonl files directly: treat as a
    #       single-element list). This lets users scope a run with e.g.
    #       --source ~/.claude/projects/-Users-mu-Business-Kenznote-kenz-note-payments
    if any(source_dir.glob("*.jsonl")):
        project_dirs = [source_dir]
    else:
        project_dirs = sorted(source_dir.iterdir())

    ingested = 0
    skipped_known = 0
    skipped_large = 0
    errors = 0

    for project_dir in project_dirs:
        if not project_dir.is_dir():
            continue

        # --- Top-level session files (<project>/<session>.jsonl) ---
        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            session_id = derive_session_id(jsonl_file)

            # Fast path: skip already-known sessions without opening the file.
            if session_id in known_ids:
                log.debug("Already known: %s", session_id)
                skipped_known += 1
                continue

            try:
                result = ingest_session(
                    jsonl_path=jsonl_file,
                    bronze_dir=bronze_dir,
                    manifest_path=manifest_path,
                    manifest=manifest,
                    dry_run=dry_run,
                )
                if result:
                    # Check whether this was a skipped-large case by inspecting
                    # file size (we cannot inspect manifest in dry-run mode).
                    try:
                        size = jsonl_file.stat().st_size
                        if size > LARGE_SESSION_BYTES_THRESHOLD:
                            skipped_large += 1
                        else:
                            ingested += 1
                            if not dry_run:
                                # Keep in-memory set in sync so a session
                                # cannot be double-processed if it appears in
                                # multiple project dirs (unlikely but safe).
                                known_ids.add(session_id)
                                manifest.append({"session_id": session_id})
                    except OSError:
                        ingested += 1
                else:
                    skipped_known += 1
            except Exception as exc:  # noqa: BLE001
                log.error("Unexpected error processing %s: %s", jsonl_file, exc)
                errors += 1

        # --- Subagent sessions (<project>/<session-uuid>/subagents/agent-*.jsonl) ---
        # Each session UUID directory may contain a subagents/ subdirectory with
        # per-agent JSONL files. We scan these separately so each subagent becomes
        # its own manifest entry linked to the parent session via parent_session.
        for session_uuid_dir in sorted(project_dir.iterdir()):
            if not session_uuid_dir.is_dir():
                continue
            subagents_dir = session_uuid_dir / "subagents"
            if not subagents_dir.is_dir():
                continue
            for agent_jsonl in sorted(subagents_dir.glob("agent-*.jsonl")):
                agent_id = derive_session_id(agent_jsonl)

                # Fast path: skip already-known subagent sessions.
                if agent_id in known_ids:
                    log.debug("Already known subagent: %s", agent_id)
                    skipped_known += 1
                    continue

                try:
                    result = ingest_subagent_session(
                        jsonl_path=agent_jsonl,
                        bronze_dir=bronze_dir,
                        manifest_path=manifest_path,
                        manifest=manifest,
                        dry_run=dry_run,
                    )
                    if result:
                        try:
                            size = agent_jsonl.stat().st_size
                            if size > LARGE_SESSION_BYTES_THRESHOLD:
                                skipped_large += 1
                            else:
                                ingested += 1
                                if not dry_run:
                                    known_ids.add(agent_id)
                                    manifest.append({"session_id": agent_id})
                        except OSError:
                            ingested += 1
                    else:
                        skipped_known += 1
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "Unexpected error processing subagent %s: %s", agent_jsonl, exc
                    )
                    errors += 1

    log.info(
        "Done. ingested=%d skipped_known=%d skipped_large=%d errors=%d dry_run=%s",
        ingested,
        skipped_known,
        skipped_large,
        errors,
        dry_run,
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
