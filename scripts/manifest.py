"""
Shared manifest read/write helpers for manifest.jsonl.

All three pipeline scripts (ingest.py, compact.py, extract.py) read and write
manifest.jsonl through this module. The schema below is frozen in the
infrastructure phase. Do NOT add fields without updating the canonical schema
in docs/product/epics/MVP-1/design.md first.

=============================================================================
FROZEN MANIFEST SCHEMA (canonical -- do not change without a design doc update)
=============================================================================

Every entry in manifest.jsonl is a JSON object on its own line (JSONL format).
Fields:

  session_id         str          Unique key. Derived from source JSONL filename.
  source_path        str          Absolute path to the original JSONL in ~/.claude/projects/.
  bronze_path        str          Absolute path to the copy in bronze/<harness>/. Layout is
                                  bronze/<harness>/YYYY-MM-DD_<session-id>.jsonl for top-level
                                  sessions, and bronze/<harness>/YYYY-MM-DD_<parent>_<agent-id>.jsonl
                                  for subagent sessions (Set by S7/S15, harness-namespaced in S15).
  meta_path          str | None   Absolute path to the bronze copy of the sibling .meta.json file.
                                  Set only for subagent sessions whose source had a .meta.json.
                                  Always null for top-level sessions. Null for subagents whose
                                  source lacked a .meta.json (rare; auto-generated subagents).
                                  Bronze must be self-contained: the manifest is a derived index;
                                  meta_path (together with bronze_path) is the authoritative record.
                                  Added in S15 (bronze self-containment refinement).
  silver_path        str | None   Absolute path to the compacted file in silver/. None until S8.
  gold_paths         list[str]    Absolute paths to extracted gold entries. [] until S9.
  status             str          Status progression (see below).
  ingested_at        str          ISO8601 timestamp when bronze copy was written.
  compacted_at       str | None   ISO8601 timestamp when silver file was written. None until S8.
  extracted_at       str | None   ISO8601 timestamp when gold entries were written. None until S9.
  bronze_size_bytes  int          Size of the bronze copy in bytes. Set by S7 (ingest).
                                  Note: canonical name is bronze_size_bytes, NOT session_size_bytes.
  silver_size_bytes  int | None   Size of the compacted silver file in bytes. None until S8.
  error              str | None   Last error message if status is "failed". None otherwise.
  harness            str          Source harness, e.g. "claude-code". Set by S7 (ingest).
  project_hash       str          The ~/.claude/projects/ directory name for the session,
                                  e.g. "-Users-mu-src-transgate-frontend". Set by S7 (ingest).
  agent              str | None   Agent type from session metadata (developer, tester, etc.).
                                  For subagent sessions: populated from agentType in the sibling
                                  .meta.json file. Null for top-level sessions that carry no
                                  agent-name record. Set by S7 (ingest).
  agent_task         str | None   Task description from the subagent's .meta.json description
                                  field. Truncated to 500 chars to keep manifest lines bounded.
                                  Null for top-level (non-subagent) sessions.
                                  Set by S15 (ingest, subagent path only).
  parent_session     str | None   UUID of the outer (parent) session if this is a subagent
                                  session. Derived from the grandparent directory name (the
                                  session UUID directory that contains subagents/). Null for
                                  top-level sessions. Set by S7/S15 (ingest).
  part_index         int | None   1-based index of this part within a chunked session. Null for
                                  unsplit sessions. Sessions whose source bytes exceed the
                                  CHUNK_SOFT_BYTES threshold are split into parts at user-turn
                                  boundaries (with a hard byte cap fallback) so that compaction
                                  can process big sessions without losing the (action, reward,
                                  adjustment) chain. Set by ingest (chunked path).
  total_parts        int | None   Total number of parts the original session was split into.
                                  Null for unsplit sessions. Same value on every part entry of
                                  a given session. Set by ingest (chunked path).
                                  Part session_id convention: <original-stem>_part_<NN>.
                                  Part grouping: strip _part_NN suffix from session_id.
  silver_offset_bytes int | None  Byte offset in the shared silver file where THIS part's
                                  contribution starts. Used by compact for re-compact safety:
                                  truncating the silver file to silver_offset_bytes restores
                                  it to the state immediately before this part was compacted.
                                  Null for unsplit entries (where the silver file is fully
                                  rewritten on each compaction). For part 1 of a chunked
                                  session it is 0 (or the offset right after the YAML
                                  frontmatter; convention: 0, the part's contribution starts
                                  at the file beginning). For parts 2..N it equals the silver
                                  file size at the start of that part's compaction call.
                                  Set by compact (M2/S2.2).
  short              bool         True if bronze_size_bytes is under the small-session threshold.
                                  Short sessions skip compaction. Set by S7 (ingest).
  memory_query       bool         True if the session is a memory-subagent query session.
                                  Tagged separately; not compacted. Set by S7 (ingest).
  cwd                str | None   Exact working directory from the first JSONL record that
                                  carries it. Disambiguates ambiguous project_hash encodings.
                                  Added in Phase A (MVP-1-14). Null for entries written before
                                  Phase A; not backfilled.
  git_branch         str | None   Git branch name from the first JSONL record that carries
                                  gitBranch. First-seen only; mid-session branch changes are
                                  not tracked. Added in Phase A (MVP-1-14). Null for entries
                                  written before Phase A or sessions without gitBranch records.
  session_started_at str | None   ISO8601 timestamp from the first non-permission-mode JSONL
                                  record. Distinct from ingested_at (which records when Hippo
                                  noticed the file). Added in Phase A (MVP-1-14). Null for
                                  entries written before Phase A.

Status progression:
  bronze -> silver -> gold

Terminal statuses:
  skipped-large   Set by S7 (ingest) if bronze_size_bytes exceeds the large-session threshold.
                  No further processing occurs.
  failed          Set by any script on unrecoverable error. The error field is populated.

=============================================================================
"""

from __future__ import annotations

import json
import os
from typing import Optional


def read_manifest(path: str) -> list[dict]:
    """Read all entries from a manifest.jsonl file.

    Args:
        path: Absolute path to the manifest.jsonl file.

    Returns:
        A list of dicts, one per line in the file. Returns an empty list if
        the file does not exist or is empty.
    """
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def append_manifest(path: str, entry: dict) -> None:
    """Append a single entry to a manifest.jsonl file (atomic append).

    Writes entry as a JSON object followed by a newline. Uses append mode so
    concurrent writers do not truncate the file. Does not validate the schema;
    callers are responsible for providing a complete entry.

    Args:
        path: Absolute path to the manifest.jsonl file.
        entry: Dict conforming to the frozen manifest schema above.
    """
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def update_manifest(path: str, session_id: str, updates: dict) -> None:
    """Update fields on an existing manifest entry identified by session_id.

    Rewrites the entire file with the updated entry in place. If no entry
    with the given session_id exists, raises KeyError.

    Args:
        path: Absolute path to the manifest.jsonl file.
        session_id: The unique session identifier to update.
        updates: Dict of fields to merge into the existing entry.

    Raises:
        KeyError: If session_id is not found in the manifest.
    """
    entries = read_manifest(path)
    found = False
    for entry in entries:
        if entry.get("session_id") == session_id:
            entry.update(updates)
            found = True
            break
    if not found:
        raise KeyError(f"session_id not found in manifest: {session_id}")
    with open(path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def find_entry(manifest: list[dict], session_id: str) -> Optional[dict]:
    """Look up a manifest entry by session_id.

    Args:
        manifest: A list of dicts as returned by read_manifest().
        session_id: The unique session identifier to find.

    Returns:
        The matching dict, or None if not found.
    """
    for entry in manifest:
        if entry.get("session_id") == session_id:
            return entry
    return None
