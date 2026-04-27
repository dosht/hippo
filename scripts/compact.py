"""
scripts/compact.py -- Silver layer compaction.

Reads bronze sessions from manifest.jsonl (status "bronze") and produces
silver summaries by calling:

  claude -p --model claude-sonnet-4-5 --max-turns 1

The compaction prompt is loaded from scripts/prompts/compact.md. Near-miss
failures and concrete configuration details must be preserved in the output.

Usage:
  python scripts/compact.py [--dry-run] [--session SESSION_ID] [--limit N]
                            [--manifest PATH] [--silver-dir DIR]
                            [--prompt PATH]

Wave 2 / Story S8. Do NOT modify manifest.py signatures or ingest.py behaviour.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.manifest import read_manifest, update_manifest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Claude model used for compaction. Do not change without a design decision.
COMPACTION_MODEL = "claude-sonnet-4-5"

# Max turns for claude -p compaction call.
COMPACTION_MAX_TURNS = 1

# Warn if compaction ratio is outside this range (ratio = silver / bronze).
# Bands recalibrated for trajectory-shape silvers (S3.2). Old bands (40%/90%)
# reflected thematic summaries; trajectory silvers are denser (more concrete
# inline detail) and target a much lower size ratio. The new floor catches
# the echo-failure mode where the model returned almost nothing — observed
# previously when claude -p partially failed and only emitted a closing line.
RATIO_WARN_LOW = 0.01   # < 1%: likely echo-failure (silver near-empty)
RATIO_WARN_HIGH = 0.30  # > 30%: trajectory likely under-compressed

# Sessions with bronze_size_bytes below this are skipped at compact time.
MIN_COMPACT_BYTES = 30_000

# Exponential backoff config for rate-limit retries.
BACKOFF_BASE_SECONDS = 2
BACKOFF_MAX_RETRIES = 3  # up to 3 retries after the initial attempt

# Default locations (resolved relative to this file's parent directory).
DEFAULT_MANIFEST = Path(__file__).parent.parent / "manifest.jsonl"
DEFAULT_SILVER_DIR = Path(__file__).parent.parent / "silver"
DEFAULT_PROMPT = (
    Path(__file__).parent / "prompts" / "compact.md"
)
# Continuation prompt for parts 2..N of a chunked session. Receives the
# existing silver content as prior context and bronze part N as input;
# outputs only the next trajectory section, appended to the silver file.
# Content shape lives in M2/S2.2; S2.1 only wires routing.
DEFAULT_CONTINUE_PROMPT = (
    Path(__file__).parent / "prompts" / "compact_continue.md"
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
    """Return the argument parser for compact.py."""
    parser = argparse.ArgumentParser(
        prog="compact.py",
        description=(
            "Process bronze sessions to silver summaries using claude -p. "
            "Skips sessions already at status silver, gold, skipped-large, or failed."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be processed without writing anything.",
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
        "--silver-dir",
        type=Path,
        default=DEFAULT_SILVER_DIR,
        metavar="DIR",
        help=f"Destination directory for silver files. Default: {DEFAULT_SILVER_DIR}",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=DEFAULT_PROMPT,
        metavar="PATH",
        help=f"Path to compaction prompt file (used for unsplit sessions and "
             f"part 1 of chunked sessions). Default: {DEFAULT_PROMPT}",
    )
    parser.add_argument(
        "--continue-prompt",
        type=Path,
        default=DEFAULT_CONTINUE_PROMPT,
        metavar="PATH",
        help=f"Path to continuation prompt for parts 2..N of chunked "
             f"sessions. Default: {DEFAULT_CONTINUE_PROMPT}",
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
    """Load the compaction prompt from scripts/prompts/compact.md.

    Args:
        prompt_path: Path to the prompt markdown file.

    Returns:
        Prompt text as a string.

    Raises:
        FileNotFoundError: If prompt_path does not exist.
    """
    if not prompt_path.exists():
        raise FileNotFoundError(f"Compaction prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _logical_session_id(entry: dict) -> str:
    """Return the logical (un-part-suffixed) session_id for an entry.

    For chunked part entries, strips the trailing ``_part_NN`` so all parts
    of a logical session share one identifier. Used to compute the shared
    silver filename. For unsplit entries, returns ``session_id`` unchanged.
    """
    sid = entry.get("session_id", "")
    if entry.get("part_index") is not None:
        return sid.rsplit("_part_", 1)[0]
    return sid


def _predecessor_session_id(entry: dict) -> str | None:
    """Return the session_id of the part immediately preceding this one.

    Returns None for unsplit entries and for part 1 (no predecessor).
    """
    pi = entry.get("part_index")
    if pi is None or pi <= 1:
        return None
    base = _logical_session_id(entry)
    return f"{base}_part_{pi - 1:02d}"


def _predecessor_is_silver(entry: dict, manifest: list[dict]) -> bool:
    """Return True if this part's predecessor has reached status=silver.

    Always True for unsplit entries and part 1 (no predecessor required).
    """
    predecessor_sid = _predecessor_session_id(entry)
    if predecessor_sid is None:
        return True
    for e in manifest:
        if e.get("session_id") == predecessor_sid:
            return e.get("status") == "silver"
    return False


def select_prompt_path(
    entry: dict,
    prompt_path: Path,
    continue_prompt_path: Path,
) -> Path:
    """Choose the right prompt for a manifest entry.

    Routing rule:
      * Unsplit sessions and part 1 of chunked sessions use the main prompt
        (`prompt_path`) -- they output a fresh trajectory from scratch.
      * Parts 2..N of chunked sessions use the continuation prompt
        (`continue_prompt_path`) -- they receive prior silver as context and
        output only the next trajectory section to append.

    The actual prior-silver context-injection logic and shared-silver-path
    handling are S2.2 deliverables; S2.1 only wires the prompt selection.

    Args:
        entry: A manifest entry dict.
        prompt_path: Path to the main (part 1 / unsplit) prompt.
        continue_prompt_path: Path to the continuation prompt.

    Returns:
        The Path to load for this entry.
    """
    part_index = entry.get("part_index")
    if part_index is not None and part_index > 1:
        return continue_prompt_path
    return prompt_path


def is_eligible(entry: dict) -> bool:
    """Return True if a manifest entry is eligible for compaction.

    Eligible: status == "bronze" AND memory_query != True AND
        (entry is a chunked part OR (short != True AND bronze_size_bytes >= MIN_COMPACT_BYTES)).

    Chunked parts (entries with non-null ``part_index``) are always eligible
    on size — even an 8KB closing tail is a continuation of an already-eligible
    session, not a "too small to be worth compacting" standalone session.

    The predecessor-must-be-silver ordering check is enforced at processing
    time (in the main loop), not here, because eligibility computed once at
    startup must allow part 2 to be considered eligible even when part 1 is
    still ``bronze`` (it will become ``silver`` mid-run).
    """
    if entry.get("status") != "bronze":
        return False
    if entry.get("memory_query") is True:
        return False
    if entry.get("part_index") is None:
        # Unsplit session: apply the standalone short/min-size rules.
        if entry.get("short") is True:
            return False
        if (entry.get("bronze_size_bytes") or 0) < MIN_COMPACT_BYTES:
            return False
    return True


def _is_rate_limit_error(returncode: int, stderr: str) -> bool:
    """Return True if the subprocess failure looks like a rate-limit error."""
    if returncode == 429:
        return True
    lowered = stderr.lower()
    return "429" in lowered or "rate limit" in lowered


def _is_transient_silent_failure(returncode: int, stderr: str) -> bool:
    """Return True if claude exited non-zero with no stderr (likely transient).

    Observed in prior runs (e.g. agent-a56f9ab108f2c12ca): claude returns a
    non-zero rc but writes nothing to stderr. Root cause is unclear but a
    transient retry is the correct general response — if the underlying
    issue is persistent the retry will exhaust and surface as failed.
    """
    return returncode != 0 and stderr.strip() == ""


def call_claude_compact(prompt_text: str, bronze_content: str) -> str:
    """Call claude -p with the compaction prompt and return the silver output.

    Writes prompt + bronze content to a temp file, then pipes it to:
      claude -p --max-turns 1 --model claude-sonnet-4-5

    Applies exponential backoff on transient failures (rate-limit errors AND
    silent non-zero exits with no stderr): base 2s, up to 3 retries (delays:
    2s, 4s, 8s).

    Args:
        prompt_text: Compaction prompt loaded from scripts/prompts/compact.md.
        bronze_content: Raw text content of the bronze JSONL file.

    Returns:
        Silver summary markdown as a string.

    Raises:
        RuntimeError: If all retries are exhausted or a non-rate-limit error occurs.
    """
    combined = prompt_text + "\n\n---\n\n" + bronze_content

    attempts = 0
    last_error: Exception | None = None

    while attempts <= BACKOFF_MAX_RETRIES:
        if attempts > 0:
            delay = BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
            log.warning(
                "Transient failure, retrying in %ds (attempt %d/%d): %s",
                delay, attempts, BACKOFF_MAX_RETRIES, last_error,
            )
            time.sleep(delay)

        try:
            result = subprocess.run(
                [
                    "claude",
                    "-p",
                    "--strict-mcp-config",
                    "--max-turns",
                    str(COMPACTION_MAX_TURNS),
                    "--model",
                    COMPACTION_MODEL,
                    combined,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to invoke claude: {exc}") from exc

        if result.returncode == 0:
            return result.stdout

        stderr = result.stderr or ""
        if _is_rate_limit_error(result.returncode, stderr):
            last_error = RuntimeError(
                f"Rate limit from claude (rc={result.returncode}): {stderr.strip()}"
            )
            attempts += 1
            continue
        if _is_transient_silent_failure(result.returncode, stderr):
            last_error = RuntimeError(
                f"Silent failure from claude (rc={result.returncode}, empty stderr)"
            )
            attempts += 1
            continue

        raise RuntimeError(
            f"claude -p failed (rc={result.returncode}): {stderr.strip()}"
        )

    raise RuntimeError(
        f"Exhausted {BACKOFF_MAX_RETRIES} retries on transient failures. Last error: {last_error}"
    )


def log_compaction_ratio(bronze_size: int, silver_size: int) -> None:
    """Log the compaction ratio and warn if outside acceptable bounds.

    Ratio = silver_size / bronze_size. Warns if ratio < RATIO_WARN_LOW or
    ratio > RATIO_WARN_HIGH.

    Args:
        bronze_size: Size of the bronze file in bytes.
        silver_size: Size of the silver file in bytes.
    """
    if bronze_size == 0:
        log.warning("Bronze size is 0; cannot compute ratio.")
        return
    ratio = silver_size / bronze_size
    log.info("Compaction ratio: %.1f%% (silver %d bytes / bronze %d bytes)", ratio * 100, silver_size, bronze_size)
    if ratio < RATIO_WARN_LOW:
        log.warning(
            "Compaction ratio %.1f%% is below %.0f%% -- session may be very dense or prompt may not be reducing enough.",
            ratio * 100,
            RATIO_WARN_LOW * 100,
        )
    elif ratio > RATIO_WARN_HIGH:
        log.warning(
            "Compaction ratio %.1f%% is above %.0f%% -- valuable details may have been lost. Review silver output.",
            ratio * 100,
            RATIO_WARN_HIGH * 100,
        )


def _project_slug(project_hash: str) -> str:
    """Derive a human-readable project name from a project_hash directory name.

    project_hash is the ~/.claude/projects/ directory name, which is the
    filesystem path with '/' replaced by '-'. Example:
      -Users-mu-src-transgate-frontend -> transgate-frontend
      -Users-mu-Business-Kenznote-kenz-note-main -> Kenznote-kenz-note-main
    """
    m = re.match(r'^-Users-[^-]+-(?:src|Business|Desktop|Documents)-(.+)$', project_hash or "")
    return m.group(1) if m else (project_hash or "")


def _silver_frontmatter(entry: dict) -> str:
    """Build YAML frontmatter string for a silver file from a manifest entry.

    Includes the three Phase A provenance fields (cwd, git_branch,
    session_started_at) when present. Includes Phase B field agent_task
    adjacent to agent and parent_session. Missing values serialize as null,
    matching the pattern used for agent and parent_session. Silver files
    written from older manifest entries that lack these fields remain valid:
    the fields will simply be null in the frontmatter.

    Args:
        entry: Manifest entry dict.

    Returns:
        YAML frontmatter string ending with a blank line, ready to prepend
        to the silver body content.
    """
    project_hash = entry.get("project_hash") or ""
    # Prefer ingest's git-derived project (worktree-aware: ~/repo/main and
    # ~/repo/payments both resolve to "repo"); fall back to path-decoded slug
    # for older manifest entries that pre-date project derivation.
    project = entry.get("project") or _project_slug(project_hash)
    agent = entry.get("agent")
    agent_task = entry.get("agent_task")
    parent = entry.get("parent_session")
    cwd = entry.get("cwd")
    git_branch = entry.get("git_branch")
    session_started_at = entry.get("session_started_at")
    lines = [
        "---",
        f"session_id: {entry.get('session_id', '')}",
        f"project_hash: {project_hash}",
        f"project: {project}",
        f"agent: {agent if agent is not None else 'null'}",
        f"agent_task: {agent_task if agent_task is not None else 'null'}",
        f"parent_session: {parent if parent is not None else 'null'}",
        f"ingested_at: {entry.get('ingested_at', '')}",
        f"harness: {entry.get('harness', 'claude-code')}",
        f"cwd: {cwd if cwd is not None else 'null'}",
        f"git_branch: {git_branch if git_branch is not None else 'null'}",
        f"session_started_at: {session_started_at if session_started_at is not None else 'null'}",
        "---",
        "",
    ]
    return "\n".join(lines)


def _silver_filename(entry: dict) -> str:
    """Return the silver output filename for the given manifest entry.

    Format: ``YYYY-MM-DD_<logical_session_id>.md`` using ingested_at date
    (fallback to today). For chunked sessions, all parts share one filename
    via the logical (un-part-suffixed) session_id, so each part appends to
    the same silver file.
    """
    logical_sid = _logical_session_id(entry) or "unknown"
    ingested_at = entry.get("ingested_at")
    if ingested_at:
        try:
            date_str = ingested_at[:10]  # take YYYY-MM-DD prefix
        except (TypeError, IndexError):
            date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    else:
        date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return f"{date_str}_{logical_sid}.md"


def _build_continuation_input(
    prior_silver_text: str,
    bronze_content: str,
    part_index: int,
    total_parts: int,
) -> str:
    """Wrap inputs for the continuation prompt as XML-tagged blocks.

    The continuation prompt expects ``<prior-silver>``, ``<bronze-part>``,
    and ``<part-info>`` tags. Each is shown verbatim to the model.
    """
    return (
        f"<prior-silver>\n{prior_silver_text}\n</prior-silver>\n\n"
        f"<part-info>part_index={part_index} total_parts={total_parts}</part-info>\n\n"
        f"<bronze-part>\n{bronze_content}\n</bronze-part>"
    )


def compact_session(
    entry: dict,
    bronze_dir: Path,
    silver_dir: Path,
    manifest_path: Path,
    playbook_path: Path,
    dry_run: bool,
    continue_playbook_path: Path | None = None,
) -> None:
    """Compact one manifest entry into a silver summary.

    Three flows:

    1. **Unsplit session** (``part_index is None``): load main prompt, run
       claude, write a fresh silver file with YAML frontmatter prepended.
    2. **Part 1 of a chunked session**: same as unsplit, but the silver
       filename uses the logical session_id (no ``_part_NN`` suffix), and
       the manifest entry's ``silver_offset_bytes`` is set to 0.
    3. **Part N>1 of a chunked session**: load continuation prompt; read
       the existing silver file (from part 1..N-1) as ``<prior-silver>``;
       wrap bronze part N as ``<bronze-part>`` plus ``<part-info>``; run
       claude; **append** output to the same silver file. Records
       ``silver_offset_bytes = file size at start of call`` so re-compact
       can truncate cleanly.

    Args:
        entry: Manifest entry dict (must have status "bronze").
        bronze_dir: Root directory for bronze files (logging only).
        silver_dir: Destination directory for silver files.
        manifest_path: Path to manifest.jsonl for status updates.
        playbook_path: Path to the main compaction prompt.
        continue_playbook_path: Path to the continuation prompt.
        dry_run: If True, log actions without writing anything.
    """
    session_id = entry.get("session_id", "<unknown>")
    bronze_path_str = entry.get("bronze_path", "")
    bronze_path = Path(bronze_path_str)
    part_index = entry.get("part_index")
    total_parts = entry.get("total_parts")
    is_continuation = part_index is not None and part_index > 1

    silver_filename = _silver_filename(entry)
    silver_dir.mkdir(parents=True, exist_ok=True)
    silver_path = silver_dir / silver_filename

    if dry_run:
        flow = (
            f"continuation (part {part_index}/{total_parts})"
            if is_continuation
            else f"part 1 of {total_parts}" if part_index is not None
            else "unsplit"
        )
        log.info("[dry-run] Would compact %s [%s] -> %s", session_id, flow, silver_path)
        return

    bronze_content = bronze_path.read_text(encoding="utf-8")
    bronze_size = len(bronze_content.encode("utf-8"))

    if is_continuation:
        if continue_playbook_path is None:
            continue_playbook_path = DEFAULT_CONTINUE_PROMPT
        prompt_text = load_prompt(continue_playbook_path)
        if not silver_path.exists():
            raise RuntimeError(
                f"Continuation requested for {session_id} but silver file "
                f"{silver_path} does not exist (predecessor likely not compacted)."
            )
        prior_silver_text = silver_path.read_text(encoding="utf-8")
        offset = silver_path.stat().st_size  # bytes before our append
        model_input = _build_continuation_input(
            prior_silver_text, bronze_content, part_index, total_parts or 0
        )
        new_section = call_claude_compact(prompt_text, model_input)
        # Ensure a clean separator so step blocks don't collide.
        sep = "" if prior_silver_text.endswith("\n\n") else ("\n" if prior_silver_text.endswith("\n") else "\n\n")
        with open(silver_path, "a", encoding="utf-8") as fh:
            fh.write(sep + new_section)
    else:
        prompt_text = load_prompt(playbook_path)
        # For part 1 of a chunked session, frontmatter uses the logical session_id
        # (the silver file represents the whole logical session, not just part 1).
        entry_for_frontmatter = (
            {**entry, "session_id": _logical_session_id(entry)}
            if part_index is not None
            else entry
        )
        silver_body = call_claude_compact(prompt_text, bronze_content)
        silver_path.write_text(
            _silver_frontmatter(entry_for_frontmatter) + silver_body,
            encoding="utf-8",
        )
        offset = 0  # part-1 / unsplit contributions start at file beginning

    silver_size = silver_path.stat().st_size
    log_compaction_ratio(bronze_size, silver_size)

    compacted_at = datetime.now(tz=timezone.utc).isoformat()
    updates = {
        "status": "silver",
        "silver_path": str(silver_path),
        "compacted_at": compacted_at,
        "silver_size_bytes": silver_size,
        "error": None,
    }
    if part_index is not None:
        updates["silver_offset_bytes"] = offset
    update_manifest(str(manifest_path), session_id, updates)

    log.info(
        "Compacted %s -> %s (silver_size=%d, %s)",
        session_id, silver_path, silver_size,
        f"part {part_index}/{total_parts} appended" if is_continuation
        else f"part 1/{total_parts} fresh" if part_index is not None
        else "unsplit",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Run compaction. Returns exit code (0 success, non-zero on error)."""
    parser = build_parser()
    args = parser.parse_args(argv)

    manifest_path: Path = args.manifest
    silver_dir: Path = args.silver_dir
    playbook_path: Path = args.prompt
    continue_playbook_path: Path = args.continue_prompt
    dry_run: bool = args.dry_run
    session_filter: str | None = args.session
    limit: int | None = args.limit

    # Resolve bronze_dir as sibling of silver_dir (convention: bronze/ next to silver/).
    bronze_dir = manifest_path.parent / "bronze"

    entries = read_manifest(str(manifest_path))

    if session_filter:
        entries = [e for e in entries if e.get("session_id") == session_filter]
        if not entries:
            log.error("No manifest entry found for session_id=%s", session_filter)
            return 1

    eligible = [e for e in entries if is_eligible(e)]

    if not eligible:
        log.info("No eligible bronze sessions to compact.")
        return 0

    # Sort so all parts of a logical session process consecutively in order.
    # Unsplit sessions interleave; their part_index is None, sorted as -1
    # so they come before parts of any session sharing their logical id
    # (no collision in practice — unsplit and chunked don't share ids).
    eligible.sort(
        key=lambda e: (_logical_session_id(e), e.get("part_index") or 0)
    )

    if limit is not None:
        eligible = eligible[:limit]

    total = len(eligible)
    log.info("Found %d eligible session(s) to compact.", total)

    errors = 0
    deferred = 0
    for i, entry in enumerate(eligible, start=1):
        session_id = entry.get("session_id", "<unknown>")

        # Predecessor-must-be-silver check at processing time. Re-read manifest
        # so that earlier successes in this run are visible.
        if entry.get("part_index") is not None and entry["part_index"] > 1:
            current_manifest = read_manifest(str(manifest_path))
            if not _predecessor_is_silver(entry, current_manifest):
                pred = _predecessor_session_id(entry)
                log.warning(
                    "[%d/%d] Deferring %s: predecessor %s not yet silver.",
                    i, total, session_id, pred,
                )
                deferred += 1
                continue

        log.info("[%d/%d] Compacting %s ...", i, total, session_id)
        try:
            compact_session(
                entry=entry,
                bronze_dir=bronze_dir,
                silver_dir=silver_dir,
                manifest_path=manifest_path,
                playbook_path=playbook_path,
                continue_playbook_path=continue_playbook_path,
                dry_run=dry_run,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to compact session %s: %s", session_id, exc)
            errors += 1
            if not dry_run:
                try:
                    update_manifest(
                        str(manifest_path),
                        session_id,
                        {"status": "failed", "error": str(exc)},
                    )
                except Exception as update_exc:
                    log.error("Could not update manifest for failed session %s: %s", session_id, update_exc)
    if deferred:
        log.info("%d part(s) deferred (predecessor not silver). Re-run after resolving.", deferred)

    if errors:
        log.warning("%d session(s) failed compaction.", errors)

    return 0


if __name__ == "__main__":
    sys.exit(main())
