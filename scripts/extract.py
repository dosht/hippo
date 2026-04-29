"""
scripts/extract.py -- Gold layer extraction.

Reads silver summaries from manifest.jsonl (status "silver") and extracts
knowledge entries by calling:

  claude -p --model claude-sonnet-4-5 --max-turns 1

The extraction prompt is loaded from scripts/prompts/extract.md. Before
writing each proposed entry, a duplicate check is performed via:

  qmd query "{proposed id and topics}" --collection hippo --json -n 3

Entries with similarity score above DUPLICATE_THRESHOLD (0.85) are skipped.
After all entries for a run are written, `qmd update && qmd embed` is called
once -- NOT per entry.

Usage:
  python scripts/extract.py [--dry-run] [--manifest PATH]
                            [--gold-dir DIR] [--prompt PATH]
                            [--limit N] [--session SESSION_ID]

Wave 3b / Story S9. Depends on S8 (compact.py) and S2 (QMD collection).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.errors import QuotaExhaustedError, is_rate_limit_error, is_transient_silent_failure
from scripts.manifest import read_manifest, update_manifest


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Claude model used for extraction. Matches compaction model (sonnet for quality).
EXTRACTION_MODEL = "claude-sonnet-4-5"
EXTRACTION_MAX_TURNS = 1

# Exponential backoff config for transient subprocess failures (rate-limit
# OR silent rc!=0/empty-stderr — same policy as scripts/compact.py:S2.3).
BACKOFF_BASE_SECONDS = 2
BACKOFF_MAX_RETRIES = 3

# QMD similarity threshold above which a proposed entry is treated as a duplicate.
# Do NOT change without updating docs/product/epics/HIPPO-MVP/design.md.
DUPLICATE_THRESHOLD = 0.85

# QMD collection name. Must match the name used in S2 and the git post-merge hook (S3).
QMD_COLLECTION = "hippo"

# Number of QMD results to retrieve for duplicate checking.
QMD_DUPLICATE_CHECK_N = 3

# Default confidence for auto-extracted entries (per design spec).
DEFAULT_CONFIDENCE = "medium"

# Default locations
DEFAULT_MANIFEST = Path(__file__).parent.parent / "manifest.jsonl"
DEFAULT_GOLD_DIR = Path(
    os.environ.get("HIPPO_GOLD_DIR")
    or Path(__file__).parent.parent / "gold" / "entries"
)
DEFAULT_PROMPT = (
    Path(__file__).parent / "prompts" / "extract.md"
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for extract.py."""
    parser = argparse.ArgumentParser(
        prog="extract.py",
        description=(
            "Process silver summaries to gold entries using claude -p. "
            "Skips sessions already at status gold, skipped-large, or failed. "
            "Runs duplicate detection via qmd query before writing each entry."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be extracted without writing anything.",
    )
    parser.add_argument(
        "--session",
        metavar="SESSION_ID",
        default=None,
        help="Process only the session with this session_id.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        metavar="PATH",
        help=f"Path to manifest.jsonl. Default: {DEFAULT_MANIFEST}",
    )
    parser.add_argument(
        "--gold-dir",
        type=Path,
        default=DEFAULT_GOLD_DIR,
        metavar="DIR",
        help=f"Destination directory for gold entries. Default: {DEFAULT_GOLD_DIR}",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=DEFAULT_PROMPT,
        metavar="PATH",
        help=f"Path to extraction prompt file. Default: {DEFAULT_PROMPT}",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process at most N sessions (useful for validation runs).",
    )
    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_prompt(prompt_path: Path) -> str:
    """Load the extraction prompt from scripts/prompts/extract.md.

    Args:
        prompt_path: Path to the prompt markdown file.

    Returns:
        Prompt text as a string.

    Raises:
        FileNotFoundError: If prompt_path does not exist.
    """
    if not prompt_path.exists():
        raise FileNotFoundError(f"Extraction prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def is_eligible(entry: dict) -> bool:
    """Return True if a manifest entry is eligible for extraction.

    Eligible: status == "silver".

    Args:
        entry: A manifest entry dict.

    Returns:
        True if the entry should be extracted.
    """
    return entry.get("status") == "silver"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a markdown document into (frontmatter dict, body string).

    If no frontmatter delimiters found, returns ({}, text).
    Handles null values and inline list values using only stdlib.

    Args:
        text: Full text of the document.

    Returns:
        Tuple of (metadata dict, body string). Body has leading newlines stripped.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, text
    fm_lines = lines[1:end]
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    metadata: dict = {}
    for line in fm_lines:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "null":
            metadata[key] = None
        elif val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            metadata[key] = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
        else:
            metadata[key] = val
    return metadata, body


def _parse_entry_block(block: str) -> dict:
    """Parse a single entry block (text between ENTRY START and ENTRY END markers).

    Extracts YAML frontmatter and markdown body. Uses _parse_frontmatter internally.

    Args:
        block: Raw text of one entry block.

    Returns:
        Dict with all frontmatter fields plus a 'body' key.

    Raises:
        ValueError: If the block is malformed (missing delimiters, id, or type).
    """
    stripped = block.strip()

    # Find the first and second '---' delimiter lines (keep legacy logic for
    # integer coercion and empty-list handling not in _parse_frontmatter).
    lines = stripped.splitlines()
    dash_indices = [i for i, line in enumerate(lines) if line.strip() == "---"]
    if len(dash_indices) < 2:
        raise ValueError("Malformed entry block: missing frontmatter delimiters (---)")

    first_dash = dash_indices[0]
    second_dash = dash_indices[1]

    frontmatter_lines = lines[first_dash + 1:second_dash]
    body_lines = lines[second_dash + 1:]

    entry: dict = {}

    for line in frontmatter_lines:
        if ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()

        if not key:
            continue

        # null value
        if raw_value == "null":
            entry[key] = None
        # list value: [item1, item2, ...]
        elif raw_value.startswith("[") and raw_value.endswith("]"):
            inner = raw_value[1:-1]
            if inner.strip() == "":
                entry[key] = []
            else:
                items = [item.strip().strip("'\"") for item in inner.split(",")]
                entry[key] = [item for item in items if item]
        else:
            # Try integer
            try:
                entry[key] = int(raw_value)
            except ValueError:
                entry[key] = raw_value

    if not entry.get("id"):
        raise ValueError("Malformed entry block: missing required field 'id'")
    if not entry.get("type"):
        raise ValueError("Malformed entry block: missing required field 'type'")

    entry["body"] = "\n".join(body_lines)
    return entry


def call_claude_extract(
    silver_path: Path,
    prompt: str,
    session_id: str,
    metadata: dict,
    silver_body: str,
    dry_run: bool,
) -> list[dict]:
    """Call claude -p with the extraction prompt and parse proposed entries.

    Command:
      claude -p --model claude-sonnet-4-5 --max-turns 1 <prompt+content>

    If claude -p returns "NO_NEW_KNOWLEDGE", returns an empty list.

    Args:
        silver_path: Path to the silver summary markdown file (used for logging only).
        prompt: Extraction prompt text from scripts/prompts/extract.md.
        session_id: Session UUID to inject into the source_sessions field.
        metadata: Dict of silver frontmatter metadata (project, agent, etc.).
        silver_body: Silver content with frontmatter already stripped.
        dry_run: If True, return an empty list without calling claude.

    Returns:
        List of proposed entry dicts (id, frontmatter fields, body).
    """
    if dry_run:
        return []

    project = metadata.get("project") or "unknown"
    agent_val = metadata.get("agent") or "unknown"
    agent_task_val = metadata.get("agent_task")
    git_branch_val = metadata.get("git_branch")
    session_started_at_val = metadata.get("session_started_at")

    thread_id_val = metadata.get("thread_id")

    meta_lines = [
        "Session metadata:",
        f"  session_id: {session_id}",
        f"  project: {project}",
        f"  agent: {agent_val}",
    ]
    if agent_task_val and agent_task_val != "null":
        meta_lines.append(f"  agent_task: {agent_task_val}")
    if git_branch_val and git_branch_val != "null":
        meta_lines.append(f"  git_branch: {git_branch_val}")
    if session_started_at_val and session_started_at_val != "null":
        meta_lines.append(f"  session_started_at: {session_started_at_val}")
    if thread_id_val and thread_id_val != "null":
        meta_lines.append(f"  thread_id: {thread_id_val}")

    combined = (
        prompt
        + "\n\n"
        + "\n".join(meta_lines)
        + "\n\n---\n\n"
        + silver_body
    )

    # Retry on transient failures: rate-limit OR silent rc!=0 with empty
    # stderr (same policy as compact.py S2.3 — quota/network blips can
    # surface either way; persistent issues exhaust and surface as failed).
    attempts = 0
    last_error: Exception | None = None
    result = None
    while attempts <= BACKOFF_MAX_RETRIES:
        if attempts > 0:
            delay = BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
            log.warning(
                "Transient failure, retrying in %ds (attempt %d/%d): %s",
                delay, attempts, BACKOFF_MAX_RETRIES, last_error,
            )
            time.sleep(delay)
        result = subprocess.run(
            [
                "claude",
                "-p",
                "--strict-mcp-config",
                "--max-turns",
                str(EXTRACTION_MAX_TURNS),
                "--model",
                EXTRACTION_MODEL,
                combined,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            break
        stderr = result.stderr or ""
        if is_rate_limit_error(result.returncode, stderr):
            last_error = QuotaExhaustedError(
                f"Rate limit from claude (rc={result.returncode}): {stderr.strip()}"
            )
            attempts += 1
            continue
        if is_transient_silent_failure(result.returncode, stderr):
            last_error = QuotaExhaustedError(
                f"Silent failure from claude (rc={result.returncode}, empty stderr)"
            )
            attempts += 1
            continue
        # Non-transient: fail fast.
        raise RuntimeError(
            f"claude -p failed (rc={result.returncode}): {stderr.strip()}"
        )
    else:
        # Retries exhausted: raise the last QuotaExhaustedError so the caller
        # recognises this as a quota stop rather than a session failure.
        raise last_error  # type: ignore[misc]

    if result is None or result.returncode != 0:
        # Defensive: should be unreachable given the loop control flow above.
        stderr = (result.stderr if result else "") or ""
        raise RuntimeError(
            f"claude -p failed (rc={getattr(result, 'returncode', '?')}): {stderr.strip()}"
        )

    output = result.stdout or ""

    if "NO_NEW_KNOWLEDGE" in output:
        return []

    entries: list[dict] = []
    # Split on entry markers
    parts = output.split("===ENTRY START===")
    for part in parts[1:]:  # skip the leading text before first marker
        if "===ENTRY END===" in part:
            raw_block, _, _ = part.partition("===ENTRY END===")
            try:
                entry = _parse_entry_block(raw_block)
                entries.append(entry)
            except ValueError as exc:
                log.warning("Failed to parse entry block: %s", exc)

    return entries


def is_duplicate(proposed_entry: dict) -> bool:
    """Check if a proposed entry is a near-duplicate of an existing gold entry.

    Runs:
      qmd query "{proposed id and topics}" --collection hippo --json -n 3

    Returns True if any result has similarity score >= DUPLICATE_THRESHOLD.

    Args:
        proposed_entry: Dict with at least 'id' and 'topics' fields.

    Returns:
        True if the entry should be skipped (duplicate detected).
    """
    entry_id = proposed_entry.get("id", "")
    topics = proposed_entry.get("topics", [])
    if isinstance(topics, list):
        topics_str = " ".join(topics)
    else:
        topics_str = str(topics)

    query = f"{entry_id} {topics_str}".strip()

    result = subprocess.run(
        [
            "qmd",
            "query",
            query,
            "--collection",
            QMD_COLLECTION,
            "--json",
            "-n",
            str(QMD_DUPLICATE_CHECK_N),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0 or not result.stdout.strip():
        # Cannot check; proceed (assume not duplicate)
        return False

    try:
        results = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        log.warning("Failed to parse qmd JSON output for duplicate check of '%s'", entry_id)
        return False

    if not isinstance(results, list):
        return False

    for item in results:
        if isinstance(item, dict):
            score = item.get("score")
            if score is not None and score >= DUPLICATE_THRESHOLD:
                return True

    return False


# Frontmatter ownership split:
#  * MODEL_OWNED — semantic fields the model decides per-entry from silver content.
#    Python validates these are present in the model's output.
#  * PYTHON_OWNED — deterministic fields Python sets/overrides post-parse so the
#    schema can't drift, dates can't be hallucinated, and the model doesn't waste
#    tokens emitting fixed values.
#
# `projects` is Python-owned: the source project is authoritative from silver
# metadata, not the model's guess. The model still emits it (required for
# validation) but Python overwrites it after parsing.
MODEL_OWNED_FIELDS = frozenset({
    "id",
    "type",
    "topics",
    "summary",
    "projects",
    "agents",
    "staleness_policy",
})
# Required subset: summary is optional (backwards compat with entries that predate it).
MODEL_OWNED_REQUIRED_FRONTMATTER = frozenset({
    "id",
    "type",
    "topics",
    "projects",
    "agents",
    "staleness_policy",
})
# Body is not frontmatter but is required alongside it.
MODEL_OWNED_REQUIRED = MODEL_OWNED_REQUIRED_FRONTMATTER | {"body"}


def validate_frontmatter(entry: dict) -> bool:
    """Validate that a proposed entry has all required model-owned fields.

    Python-owned fields (source_sessions, created, last_validated, last_queried,
    query_count, supersedes, confidence) are filled in by ``_apply_python_owned_fields``
    after parsing, so the model is not required to emit them.

    Args:
        entry: Dict representing a proposed gold entry.

    Returns:
        True if all required model-owned fields are present.
    """
    return MODEL_OWNED_REQUIRED.issubset(entry.keys())


def _apply_python_owned_fields(entry: dict, session_id: str, project: str | None = None, thread_id: str | None = None) -> dict:
    """Fill in the deterministic frontmatter fields Python owns.

    Mutates and returns ``entry``. Always overrides — even if the model emitted
    these fields, Python's values win.

    Fields:
      source_sessions: [session_id]
      projects: [project] if project is known, else kept as model emitted
      created, last_validated: today's UTC date (YYYY-MM-DD)
      last_queried: null
      query_count: 0
      supersedes: [] (unless caller already set non-empty list)
      confidence: "medium" (default for auto-extracted; model may not lower it)

    Args:
        entry: Proposed gold entry dict from the model.
        session_id: Source session_id for the source_sessions list.
        project: Project name from silver metadata. When provided, overwrites
            any ``projects`` value the model emitted so the field is always
            authoritative. Pass None only when project is genuinely unknown.

    Returns:
        The same dict with Python-owned fields populated.
    """
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    entry["source_sessions"] = [session_id]
    if project and project not in ("unknown", ""):
        entry["projects"] = [project]
    entry["created"] = today
    entry["last_validated"] = today
    entry["last_queried"] = None
    entry["query_count"] = 0
    entry["confidence"] = "medium"
    if not entry.get("supersedes"):
        entry["supersedes"] = []
    # thread_id: Python-owned, carries the thread identity through to gold.
    # Serializes as null when absent (pre-MVP-2 rows have no thread_id).
    entry["thread_id"] = thread_id
    return entry


def _render_frontmatter_value(value: object) -> str:
    """Render a single frontmatter value to its YAML flow-style string."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        items = ", ".join(str(item) for item in value)
        return f"[{items}]"
    return str(value)


def write_gold_entry(entry: dict, gold_dir: Path, dry_run: bool) -> Path:
    """Write a gold entry to gold/entries/<entry-id>.md.

    Args:
        entry: Dict with all required frontmatter fields and markdown body.
        gold_dir: Destination directory.
        dry_run: If True, log the write without creating the file.

    Returns:
        Absolute path to the written file (or the would-be path in dry_run).
    """
    entry_id = entry["id"]
    output_path = gold_dir / f"{entry_id}.md"

    # Build frontmatter string (all fields except 'body')
    frontmatter_lines = []
    for key, value in entry.items():
        if key == "body":
            continue
        frontmatter_lines.append(f"{key}: {_render_frontmatter_value(value)}")

    frontmatter = "\n".join(frontmatter_lines)
    body = entry.get("body", "").strip()
    file_content = f"---\n{frontmatter}\n---\n\n{body}\n"

    if dry_run:
        log.info("[dry-run] Would write gold entry: %s", output_path)
        return output_path.resolve()

    gold_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(file_content, encoding="utf-8")
    log.info("Wrote gold entry: %s", output_path)
    return output_path.resolve()


def reindex_qmd(dry_run: bool) -> None:
    """Run `qmd update && qmd embed` once after all entries for a run are written.

    This must be called once per extraction run, NOT once per entry.
    Uses QMD_COLLECTION constant.

    Args:
        dry_run: If True, log the command without executing it.
    """
    if dry_run:
        log.info("[dry-run] Would run: qmd update && qmd embed")
        return

    log.info("Running: qmd update --collection %s", QMD_COLLECTION)
    update_result = subprocess.run(
        ["qmd", "update", "--collection", QMD_COLLECTION],
        check=False,
    )
    if update_result.returncode != 0:
        log.warning("qmd update failed (rc=%d)", update_result.returncode)

    log.info("Running: qmd embed --collection %s", QMD_COLLECTION)
    embed_result = subprocess.run(
        ["qmd", "embed", "--collection", QMD_COLLECTION],
        check=False,
    )
    if embed_result.returncode != 0:
        log.warning("qmd embed failed (rc=%d)", embed_result.returncode)


def extract_session(
    entry: dict,
    gold_dir: Path,
    manifest_path: Path,
    prompt: str,
    dry_run: bool,
) -> list[str]:
    """Extract gold entries from a single silver session.

    Updates manifest entry with status "gold", extracted_at, and gold_paths.
    If claude -p returns no knowledge, sets gold_paths to [].

    Args:
        entry: Manifest entry dict (must have status "silver").
        gold_dir: Destination directory for gold entries.
        manifest_path: Path to manifest.jsonl for status updates.
        prompt: Extraction prompt text.
        dry_run: If True, log actions without writing anything.

    Returns:
        List of absolute paths to written gold entry files.
    """
    session_id = entry["session_id"]
    silver_path = Path(entry["silver_path"])

    silver_content = silver_path.read_text(encoding="utf-8")
    silver_meta, silver_body = _parse_frontmatter(silver_content)

    # Backward compatibility: if silver file has no frontmatter (old files),
    # fall back to reading project/agent from the manifest entry.
    if not silver_meta:
        silver_meta = {
            "project": entry.get("project_hash") or "",
            "agent": entry.get("agent"),
        }

    proposed_entries = call_claude_extract(
        silver_path, prompt, session_id, silver_meta, silver_body, dry_run
    )

    written_paths: list[str] = []

    if not proposed_entries:
        log.info("No new knowledge extracted from session %s", session_id)
    else:
        for proposed in proposed_entries:
            # Validate model-owned fields first (the only ones the model is
            # responsible for); Python-owned fields are filled in after.
            if not validate_frontmatter(proposed):
                log.warning(
                    "Skipping entry with missing model-owned fields (session %s, id=%s)",
                    session_id,
                    proposed.get("id", "<unknown>"),
                )
                continue

            # Override Python-owned deterministic fields. Anything the model
            # wrote for these is discarded (prevents drift and date errors).
            _apply_python_owned_fields(
                proposed,
                session_id,
                project=silver_meta.get("project"),
                thread_id=silver_meta.get("thread_id") or None,
            )

            if is_duplicate(proposed):
                log.info("Skipping duplicate: %s", proposed.get("id"))
                continue

            written_path = write_gold_entry(proposed, gold_dir, dry_run)
            written_paths.append(str(written_path))

    now_iso = datetime.now(tz=timezone.utc).isoformat()
    if not dry_run:
        update_manifest(
            str(manifest_path),
            session_id,
            {
                "status": "gold",
                "extracted_at": now_iso,
                "gold_paths": written_paths,
                "error": None,
            },
        )

    return written_paths


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Run extraction. Returns exit code (0 success, non-zero on error).

    After all sessions are processed, calls reindex_qmd() once.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    manifest_path: Path = args.manifest
    gold_dir: Path = args.gold_dir
    prompt_path: Path = args.prompt
    dry_run: bool = args.dry_run
    session_filter: str | None = args.session
    limit: int | None = args.limit

    prompt = load_prompt(prompt_path)

    entries = read_manifest(str(manifest_path))

    if session_filter:
        entries = [e for e in entries if e.get("session_id") == session_filter]
        if not entries:
            log.error("No manifest entry found for session_id=%s", session_filter)
            return 1

    eligible = [e for e in entries if is_eligible(e)]

    if not eligible:
        log.info("No eligible silver sessions to extract.")
        return 0

    # Dedupe by silver_path: chunked sessions have one shared silver file
    # across N part-entries, so naive iteration would extract N times against
    # the same content. Group eligible entries by silver_path; pick one
    # canonical entry per group (highest part_index, or the unsplit entry);
    # update all entries in the group to gold after extraction.
    groups: dict[str, list[dict]] = {}
    for e in eligible:
        sp = e.get("silver_path") or ""
        if not sp:
            continue
        groups.setdefault(sp, []).append(e)

    canonical_entries: list[tuple[dict, list[dict]]] = []
    for sp, group in groups.items():
        # Canonical = highest part_index in group (None treated as 0 / unsplit).
        canonical = max(group, key=lambda x: (x.get("part_index") or 0))
        canonical_entries.append((canonical, group))

    if limit is not None:
        canonical_entries = canonical_entries[:limit]

    total = len(canonical_entries)
    log.info(
        "Found %d unique silver file(s) to extract from (%d eligible entries grouped).",
        total, len(eligible),
    )

    errors = 0
    total_written = 0
    any_written = False

    for i, (entry, group) in enumerate(canonical_entries, start=1):
        session_id = entry.get("session_id", "<unknown>")
        log.info("[%d/%d] Extracting session %s ...", i, total, session_id)
        try:
            written = extract_session(
                entry=entry,
                gold_dir=gold_dir,
                manifest_path=manifest_path,
                prompt=prompt,
                dry_run=dry_run,
            )
            total_written += len(written)
            if written:
                any_written = True
            # After a successful extract on the canonical entry, also
            # advance the other parts of this logical session to gold so
            # they don't get re-extracted on a future run.
            if not dry_run:
                now_iso = datetime.now(tz=timezone.utc).isoformat()
                for sibling in group:
                    if sibling.get("session_id") == entry.get("session_id"):
                        continue  # canonical already updated by extract_session
                    try:
                        update_manifest(
                            str(manifest_path),
                            sibling["session_id"],
                            {
                                "status": "gold",
                                "extracted_at": now_iso,
                                "gold_paths": written,
                                "error": None,
                            },
                        )
                    except Exception as upd_exc:
                        log.warning(
                            "Could not propagate gold status to sibling %s: %s",
                            sibling.get("session_id"), upd_exc,
                        )
        except QuotaExhaustedError as exc:
            # Quota wall: stop the run gracefully. The session stays at its
            # current status (silver) so the next scheduled run picks it up.
            # Exit 0 so launchd does not flag the job as failed (consistent
            # with compact.py line 679 and arc42 Section 8 Quota-Aware Stop).
            log.warning("Quota wall hit at session %s: %s. Stopping run.", session_id, exc)
            break
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to extract session %s: %s", session_id, exc)
            errors += 1
            if not dry_run:
                try:
                    update_manifest(
                        str(manifest_path),
                        session_id,
                        {"status": "failed", "error": str(exc)},
                    )
                except Exception as update_exc:
                    log.error(
                        "Could not update manifest for failed session %s: %s",
                        session_id,
                        update_exc,
                    )

    if any_written and not dry_run:
        reindex_qmd(dry_run=False)
    elif dry_run:
        # Still show the dry-run reindex message if there were eligible sessions
        if eligible:
            reindex_qmd(dry_run=True)

    log.info(
        "Done. extracted=%d errors=%d dry_run=%s",
        total_written,
        errors,
        dry_run,
    )

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
