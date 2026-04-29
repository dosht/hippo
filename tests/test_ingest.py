"""Unit tests for scripts/ingest.py."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.ingest import (
    HARNESS,
    LARGE_SESSION_BYTES_THRESHOLD,
    SHORT_SESSION_BYTES_THRESHOLD,
    derive_session_id,
    extract_agent_and_parent,
    ingest_session,
    ingest_subagent_session,
    read_subagent_meta,
    main,
)
from scripts.manifest import read_manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_session(path: Path, records: list[dict]) -> None:
    """Write a list of record dicts as JSONL to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _minimal_records(session_id: str, n_messages: int = 6) -> list[dict]:
    """Produce a minimal list of JSONL records for a session."""
    records = [
        {"type": "permission-mode", "permissionMode": "acceptEdits", "sessionId": session_id},
    ]
    for i in range(n_messages):
        role = "user" if i % 2 == 0 else "assistant"
        records.append({
            "type": role,
            "uuid": f"msg-{i}",
            "sessionId": session_id,
            "message": {"role": role, "content": f"message {i}"},
        })
    return records


# ---------------------------------------------------------------------------
# derive_session_id
# ---------------------------------------------------------------------------

def test_derive_session_id_strips_extension():
    """derive_session_id returns the stem of the path."""
    p = Path("/some/project/abc-123-def.jsonl")
    assert derive_session_id(p) == "abc-123-def"


def test_derive_session_id_uuid_style():
    """derive_session_id handles UUID-format filenames."""
    p = Path("/projects/-Users-mu/98584c02-337b-4962-869c-9c6b2756f160.jsonl")
    assert derive_session_id(p) == "98584c02-337b-4962-869c-9c6b2756f160"


# ---------------------------------------------------------------------------
# extract_agent_and_parent
# ---------------------------------------------------------------------------

def test_extract_agent_present(tmp_path):
    """extract_agent_and_parent finds agentName from agent-name record."""
    f = tmp_path / "session.jsonl"
    records = [
        {"type": "agent-name", "agentName": "my-developer-agent", "sessionId": "s1"},
    ]
    _write_session(f, records)
    agent, parent = extract_agent_and_parent(f)
    assert agent == "my-developer-agent"
    assert parent is None


def test_extract_agent_absent(tmp_path):
    """extract_agent_and_parent returns None when no agent-name record exists."""
    f = tmp_path / "session.jsonl"
    _write_session(f, [{"type": "permission-mode", "permissionMode": "acceptEdits"}])
    agent, parent = extract_agent_and_parent(f)
    assert agent is None
    assert parent is None


def test_extract_agent_memory(tmp_path):
    """extract_agent_and_parent detects memory agent names."""
    f = tmp_path / "session.jsonl"
    records = [{"type": "agent-name", "agentName": "hippo-memory-subagent"}]
    _write_session(f, records)
    agent, _ = extract_agent_and_parent(f)
    assert agent == "hippo-memory-subagent"


def test_extract_agent_missing_file():
    """extract_agent_and_parent returns (None, None) for a missing file."""
    agent, parent = extract_agent_and_parent(Path("/nonexistent/file.jsonl"))
    assert agent is None
    assert parent is None


# ---------------------------------------------------------------------------
# ingest_session -- basic flow
# ---------------------------------------------------------------------------

def test_ingest_session_new(tmp_path):
    """ingest_session copies file to bronze and appends manifest entry."""
    src_dir = tmp_path / "projects" / "-Users-mu"
    session_id = "test-session-001"
    src_file = src_dir / f"{session_id}.jsonl"
    _write_session(src_file, _minimal_records(session_id))

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    result = ingest_session(
        jsonl_path=src_file,
        bronze_dir=bronze_dir,
        manifest_path=manifest_path,
        manifest=[],
        dry_run=False,
    )

    assert result is True
    entries = read_manifest(str(manifest_path))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["session_id"] == session_id
    assert entry["status"] == "bronze"
    assert entry["harness"] == "claude-code"
    assert entry["project_hash"] == "-Users-mu"
    assert entry["silver_path"] is None
    assert entry["gold_paths"] == []
    assert entry["compacted_at"] is None
    assert entry["extracted_at"] is None
    assert entry["error"] is None
    # Top-level sessions always have meta_path=null.
    assert entry["meta_path"] is None

    # Bronze file should exist in the harness-namespaced subdir.
    harness_bronze_dir = bronze_dir / HARNESS
    bronze_files = list(harness_bronze_dir.glob("*.jsonl"))
    assert len(bronze_files) == 1
    assert session_id in bronze_files[0].name
    assert f"/{HARNESS}/" in str(bronze_files[0])


def test_ingest_session_already_in_manifest(tmp_path):
    """ingest_session skips a session already present in manifest."""
    src_dir = tmp_path / "projects" / "proj"
    session_id = "already-known"
    src_file = src_dir / f"{session_id}.jsonl"
    _write_session(src_file, _minimal_records(session_id))

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"
    existing = [{"session_id": session_id, "status": "bronze"}]

    result = ingest_session(
        jsonl_path=src_file,
        bronze_dir=bronze_dir,
        manifest_path=manifest_path,
        manifest=existing,
        dry_run=False,
    )

    assert result is False
    # Manifest should remain untouched.
    assert not manifest_path.exists()


def test_ingest_session_skipped_large(tmp_path):
    """ingest_session sets status skipped-large for oversized files."""
    src_dir = tmp_path / "projects" / "proj"
    session_id = "large-session"
    src_file = src_dir / f"{session_id}.jsonl"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    # Write a file larger than the threshold.
    src_file.write_bytes(b"x" * (LARGE_SESSION_BYTES_THRESHOLD + 1))

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    result = ingest_session(
        jsonl_path=src_file,
        bronze_dir=bronze_dir,
        manifest_path=manifest_path,
        manifest=[],
        dry_run=False,
    )

    assert result is True
    entries = read_manifest(str(manifest_path))
    assert entries[0]["status"] == "skipped-large"
    # File must NOT be copied to bronze.
    assert not (bronze_dir / f"{session_id}.jsonl").exists()
    # bronze_dir should not exist at all (skipped-large never creates it).
    assert not bronze_dir.exists()


def test_ingest_session_short_flag(tmp_path):
    """ingest_session sets short=True for small files."""
    src_dir = tmp_path / "projects" / "proj"
    session_id = "tiny-session"
    src_file = src_dir / f"{session_id}.jsonl"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    # Write a file smaller than the short threshold.
    src_file.write_bytes(b'{"type":"user"}\n' * 2)

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    ingest_session(
        jsonl_path=src_file,
        bronze_dir=bronze_dir,
        manifest_path=manifest_path,
        manifest=[],
        dry_run=False,
    )

    entries = read_manifest(str(manifest_path))
    assert entries[0]["short"] is True


def test_ingest_session_memory_query_flag(tmp_path):
    """ingest_session sets memory_query=True when agent name contains 'memory'."""
    src_dir = tmp_path / "projects" / "proj"
    session_id = "memory-session"
    src_file = src_dir / f"{session_id}.jsonl"
    records = [
        {"type": "agent-name", "agentName": "hippo-memory-subagent", "sessionId": session_id},
    ] + _minimal_records(session_id)
    _write_session(src_file, records)

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    ingest_session(
        jsonl_path=src_file,
        bronze_dir=bronze_dir,
        manifest_path=manifest_path,
        manifest=[],
        dry_run=False,
    )

    entries = read_manifest(str(manifest_path))
    assert entries[0]["memory_query"] is True
    assert entries[0]["agent"] == "hippo-memory-subagent"


def test_ingest_session_dry_run_does_not_write(tmp_path):
    """dry_run=True does not write any files."""
    src_dir = tmp_path / "projects" / "proj"
    session_id = "dry-session"
    src_file = src_dir / f"{session_id}.jsonl"
    _write_session(src_file, _minimal_records(session_id))

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    ingest_session(
        jsonl_path=src_file,
        bronze_dir=bronze_dir,
        manifest_path=manifest_path,
        manifest=[],
        dry_run=True,
    )

    assert not manifest_path.exists()
    assert not bronze_dir.exists()


# ---------------------------------------------------------------------------
# main -- idempotency and scanning
# ---------------------------------------------------------------------------

def _build_source_tree(base: Path, sessions: list[tuple[str, str, list[dict]]]) -> None:
    """Create a fake ~/.claude/projects/ tree.

    sessions: list of (project_hash, session_id, records)
    """
    for project_hash, session_id, records in sessions:
        f = base / project_hash / f"{session_id}.jsonl"
        _write_session(f, records)


def test_main_scans_and_ingests(tmp_path):
    """main ingests all new sessions found in the source directory."""
    source = tmp_path / "projects"
    bronze = tmp_path / "bronze"
    manifest = tmp_path / "manifest.jsonl"

    sessions = [
        ("-Users-mu-proj-a", "sess-aaa", _minimal_records("sess-aaa")),
        ("-Users-mu-proj-b", "sess-bbb", _minimal_records("sess-bbb")),
    ]
    _build_source_tree(source, sessions)

    exit_code = main([
        "--source", str(source),
        "--bronze-dir", str(bronze),
        "--manifest", str(manifest),
    ])

    assert exit_code == 0
    entries = read_manifest(str(manifest))
    assert len(entries) == 2
    ids = {e["session_id"] for e in entries}
    assert ids == {"sess-aaa", "sess-bbb"}


def test_main_idempotent(tmp_path):
    """Running main twice does not produce duplicate manifest entries."""
    source = tmp_path / "projects"
    bronze = tmp_path / "bronze"
    manifest = tmp_path / "manifest.jsonl"

    sessions = [("-proj", "sess-x", _minimal_records("sess-x"))]
    _build_source_tree(source, sessions)

    main(["--source", str(source), "--bronze-dir", str(bronze), "--manifest", str(manifest)])
    main(["--source", str(source), "--bronze-dir", str(bronze), "--manifest", str(manifest)])

    entries = read_manifest(str(manifest))
    assert len(entries) == 1


def test_main_dry_run_no_writes(tmp_path):
    """--dry-run produces no output files."""
    source = tmp_path / "projects"
    bronze = tmp_path / "bronze"
    manifest = tmp_path / "manifest.jsonl"

    sessions = [("-proj", "sess-dry", _minimal_records("sess-dry"))]
    _build_source_tree(source, sessions)

    exit_code = main([
        "--source", str(source),
        "--bronze-dir", str(bronze),
        "--manifest", str(manifest),
        "--dry-run",
    ])

    assert exit_code == 0
    assert not manifest.exists()
    assert not bronze.exists()


def test_main_missing_source(tmp_path):
    """main returns non-zero exit code when source dir is missing."""
    exit_code = main([
        "--source", str(tmp_path / "nonexistent"),
        "--bronze-dir", str(tmp_path / "bronze"),
        "--manifest", str(tmp_path / "manifest.jsonl"),
    ])
    assert exit_code != 0


def test_main_all_manifest_fields_present(tmp_path):
    """Every manifest entry produced by main contains all frozen schema fields."""
    source = tmp_path / "projects"
    bronze = tmp_path / "bronze"
    manifest = tmp_path / "manifest.jsonl"

    sessions = [("-proj", "full-field-check", _minimal_records("full-field-check"))]
    _build_source_tree(source, sessions)

    main(["--source", str(source), "--bronze-dir", str(bronze), "--manifest", str(manifest)])

    entries = read_manifest(str(manifest))
    assert len(entries) == 1
    entry = entries[0]

    required_fields = {
        "session_id", "source_path", "bronze_path", "meta_path", "silver_path", "gold_paths",
        "status", "ingested_at", "compacted_at", "extracted_at",
        "bronze_size_bytes", "silver_size_bytes", "error",
        "harness", "project_hash", "agent", "agent_task", "parent_session",
        "short", "memory_query",
    }
    missing = required_fields - set(entry.keys())
    assert missing == set(), f"Missing manifest fields: {missing}"


# ---------------------------------------------------------------------------
# Subagent helpers
# ---------------------------------------------------------------------------

def _write_meta(path: Path, agent_type: str | None, description: str | None) -> None:
    """Write a .meta.json file at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if agent_type is not None:
        data["agentType"] = agent_type
    if description is not None:
        data["description"] = description
    path.write_text(json.dumps(data), encoding="utf-8")


def _build_subagent_tree(
    base: Path,
    project_hash: str,
    parent_session_uuid: str,
    agents: list[tuple[str, str | None, str | None, list[dict]]],
) -> None:
    """Create a subagent session tree under base/project_hash/parent_session_uuid/subagents/.

    agents: list of (agent_id, agent_type, description, records)
    """
    subagents_dir = base / project_hash / parent_session_uuid / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    for agent_id, agent_type, description, records in agents:
        jsonl_path = subagents_dir / f"{agent_id}.jsonl"
        _write_session(jsonl_path, records)
        meta_path = subagents_dir / f"{agent_id}.meta.json"
        _write_meta(meta_path, agent_type, description)


# ---------------------------------------------------------------------------
# read_subagent_meta
# ---------------------------------------------------------------------------

def test_read_subagent_meta_present(tmp_path):
    """read_subagent_meta parses agentType and description from a well-formed file."""
    meta = tmp_path / "agent-abc.meta.json"
    _write_meta(meta, "tech-lead", "Review payment webhook code")
    agent_type, agent_task = read_subagent_meta(meta)
    assert agent_type == "tech-lead"
    assert agent_task == "Review payment webhook code"


def test_read_subagent_meta_missing(tmp_path):
    """read_subagent_meta returns (None, None) when the file does not exist."""
    meta = tmp_path / "agent-missing.meta.json"
    agent_type, agent_task = read_subagent_meta(meta)
    assert agent_type is None
    assert agent_task is None


def test_read_subagent_meta_malformed(tmp_path):
    """read_subagent_meta returns (None, None) for invalid JSON."""
    meta = tmp_path / "agent-bad.meta.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text("{not valid json", encoding="utf-8")
    agent_type, agent_task = read_subagent_meta(meta)
    assert agent_type is None
    assert agent_task is None


def test_read_subagent_meta_truncates_long_description(tmp_path):
    """read_subagent_meta truncates description to 500 characters."""
    meta = tmp_path / "agent-long.meta.json"
    long_desc = "x" * 600
    _write_meta(meta, "developer", long_desc)
    _, agent_task = read_subagent_meta(meta)
    assert agent_task is not None
    assert len(agent_task) == 500
    assert agent_task == "x" * 500


# ---------------------------------------------------------------------------
# ingest_subagent_session -- basic flow
# ---------------------------------------------------------------------------

def test_ingest_subagent_session_new(tmp_path):
    """ingest_subagent_session copies file to bronze with correct manifest fields."""
    project_hash = "-Users-mu-proj-pay"
    parent_uuid = "550caba1-3fef-40ab-abff-8ce0176c5030"
    agent_id = "agent-a7b3a2427ff7af6b"
    agent_type = "tech-lead"
    description = "Review webhook ingestion code"

    subagents_dir = tmp_path / "projects" / project_hash / parent_uuid / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = subagents_dir / f"{agent_id}.jsonl"
    _write_session(jsonl_path, _minimal_records(agent_id))
    meta_path = subagents_dir / f"{agent_id}.meta.json"
    _write_meta(meta_path, agent_type, description)

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    result = ingest_subagent_session(
        jsonl_path=jsonl_path,
        bronze_dir=bronze_dir,
        manifest_path=manifest_path,
        manifest=[],
        dry_run=False,
    )

    assert result is True
    entries = read_manifest(str(manifest_path))
    assert len(entries) == 1
    entry = entries[0]

    assert entry["session_id"] == agent_id
    assert entry["parent_session"] == parent_uuid
    assert entry["project_hash"] == project_hash
    assert entry["agent"] == agent_type
    assert entry["agent_task"] == description
    assert entry["status"] == "bronze"
    assert entry["harness"] == "claude-code"
    assert entry["silver_path"] is None
    assert entry["gold_paths"] == []

    # Bronze filename must include parent_session and agent_id in the harness subdir.
    harness_bronze_dir = bronze_dir / HARNESS
    bronze_files = list(harness_bronze_dir.glob("*.jsonl"))
    assert len(bronze_files) == 1
    assert parent_uuid in bronze_files[0].name
    assert agent_id in bronze_files[0].name
    assert f"/{HARNESS}/" in str(bronze_files[0])


def test_ingest_subagent_session_already_in_manifest(tmp_path):
    """ingest_subagent_session skips a session already in the manifest."""
    agent_id = "agent-aabbcc112233"
    subagents_dir = tmp_path / "proj" / "parent-uuid" / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = subagents_dir / f"{agent_id}.jsonl"
    _write_session(jsonl_path, _minimal_records(agent_id))
    _write_meta(subagents_dir / f"{agent_id}.meta.json", "developer", "Build feature")

    manifest_path = tmp_path / "manifest.jsonl"
    existing = [{"session_id": agent_id, "status": "bronze"}]

    result = ingest_subagent_session(
        jsonl_path=jsonl_path,
        bronze_dir=tmp_path / "bronze",
        manifest_path=manifest_path,
        manifest=existing,
        dry_run=False,
    )

    assert result is False
    assert not manifest_path.exists()


# ---------------------------------------------------------------------------
# Subagent discovery via main()
# ---------------------------------------------------------------------------

def test_ingest_subagent_discovery(tmp_path):
    """main discovers subagent JSONLs alongside top-level sessions.

    Verifies:
    - Both top-level and subagent sessions appear in the manifest.
    - parent_session linkage is correct for the subagent entry.
    - agent and agent_task are populated from .meta.json.
    """
    source = tmp_path / "projects"
    bronze = tmp_path / "bronze"
    manifest = tmp_path / "manifest.jsonl"

    project_hash = "-Users-mu-proj-pay"
    parent_uuid = "550caba1-3fef-40ab-abff-8ce0176c5030"
    top_session_id = parent_uuid
    agent_id = "agent-a7b3a2427ff7af6b7"
    agent_type = "tech-lead"
    description = "Review webhook ingestion path"

    # Top-level session file.
    top_jsonl = source / project_hash / f"{top_session_id}.jsonl"
    _write_session(top_jsonl, _minimal_records(top_session_id))

    # Subagent session.
    _build_subagent_tree(
        source, project_hash, parent_uuid,
        [(agent_id, agent_type, description, _minimal_records(agent_id))],
    )

    exit_code = main([
        "--source", str(source),
        "--bronze-dir", str(bronze),
        "--manifest", str(manifest),
    ])

    assert exit_code == 0
    entries = read_manifest(str(manifest))
    assert len(entries) == 2

    ids = {e["session_id"] for e in entries}
    assert top_session_id in ids
    assert agent_id in ids

    subagent_entry = next(e for e in entries if e["session_id"] == agent_id)
    assert subagent_entry["parent_session"] == parent_uuid
    assert subagent_entry["project_hash"] == project_hash
    assert subagent_entry["agent"] == agent_type
    assert subagent_entry["agent_task"] == description


def test_ingest_subagent_meta_missing_from_discovery(tmp_path):
    """When .meta.json is absent, agent and agent_task are None and ingest doesn't crash."""
    source = tmp_path / "projects"
    bronze = tmp_path / "bronze"
    manifest = tmp_path / "manifest.jsonl"

    project_hash = "-Users-mu-proj-a"
    parent_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    agent_id = "agent-nometa1234567890"

    # Write JSONL but no .meta.json.
    subagents_dir = source / project_hash / parent_uuid / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    _write_session(subagents_dir / f"{agent_id}.jsonl", _minimal_records(agent_id))
    # Do NOT write .meta.json

    exit_code = main([
        "--source", str(source),
        "--bronze-dir", str(bronze),
        "--manifest", str(manifest),
    ])

    assert exit_code == 0
    entries = read_manifest(str(manifest))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["session_id"] == agent_id
    assert entry["agent"] is None
    assert entry["agent_task"] is None


def test_ingest_subagent_meta_malformed_from_discovery(tmp_path):
    """When .meta.json is malformed JSON, agent and agent_task are None and ingest doesn't crash."""
    source = tmp_path / "projects"
    bronze = tmp_path / "bronze"
    manifest = tmp_path / "manifest.jsonl"

    project_hash = "-Users-mu-proj-b"
    parent_uuid = "ffffffff-eeee-dddd-cccc-bbbbbbbbbbbb"
    agent_id = "agent-badmeta0000000"

    subagents_dir = source / project_hash / parent_uuid / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    _write_session(subagents_dir / f"{agent_id}.jsonl", _minimal_records(agent_id))
    # Write malformed JSON.
    (subagents_dir / f"{agent_id}.meta.json").write_text("{invalid", encoding="utf-8")

    exit_code = main([
        "--source", str(source),
        "--bronze-dir", str(bronze),
        "--manifest", str(manifest),
    ])

    assert exit_code == 0
    entries = read_manifest(str(manifest))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["agent"] is None
    assert entry["agent_task"] is None


def test_ingest_subagent_idempotency(tmp_path):
    """Running main twice does not duplicate subagent entries in the manifest."""
    source = tmp_path / "projects"
    bronze = tmp_path / "bronze"
    manifest = tmp_path / "manifest.jsonl"

    project_hash = "-Users-mu-proj-idem"
    parent_uuid = "11111111-2222-3333-4444-555555555555"
    agent_id = "agent-idem000000000"

    _build_subagent_tree(
        source, project_hash, parent_uuid,
        [(agent_id, "tester", "Validate payment flow", _minimal_records(agent_id))],
    )

    main(["--source", str(source), "--bronze-dir", str(bronze), "--manifest", str(manifest)])
    main(["--source", str(source), "--bronze-dir", str(bronze), "--manifest", str(manifest)])

    entries = read_manifest(str(manifest))
    agent_entries = [e for e in entries if e["session_id"] == agent_id]
    assert len(agent_entries) == 1, "Subagent entry was duplicated on second run"


def test_ingest_subagent_all_manifest_fields_present(tmp_path):
    """Every subagent manifest entry contains all required schema fields including agent_task."""
    source = tmp_path / "projects"
    bronze = tmp_path / "bronze"
    manifest = tmp_path / "manifest.jsonl"

    project_hash = "-Users-mu-proj-fields"
    parent_uuid = "aaaabbbb-cccc-dddd-eeee-ffffgggghhhh"
    agent_id = "agent-fieldscheck00"

    _build_subagent_tree(
        source, project_hash, parent_uuid,
        [(agent_id, "developer", "Implement feature X", _minimal_records(agent_id))],
    )

    main(["--source", str(source), "--bronze-dir", str(bronze), "--manifest", str(manifest)])

    entries = read_manifest(str(manifest))
    assert len(entries) == 1
    entry = entries[0]

    required_fields = {
        "session_id", "source_path", "bronze_path", "meta_path", "silver_path", "gold_paths",
        "status", "ingested_at", "compacted_at", "extracted_at",
        "bronze_size_bytes", "silver_size_bytes", "error",
        "harness", "project_hash", "agent", "agent_task", "parent_session",
        "short", "memory_query",
    }
    missing = required_fields - set(entry.keys())
    assert missing == set(), f"Missing manifest fields: {missing}"


# ---------------------------------------------------------------------------
# Bronze self-containment refinement (MVP-1-15 refinement 2026-04-25)
# ---------------------------------------------------------------------------

def test_ingest_subagent_copies_meta_json(tmp_path):
    """ingest_subagent_session copies the .meta.json sidecar into bronze and sets meta_path."""
    project_hash = "-Users-mu-proj-meta"
    parent_uuid = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
    agent_id = "agent-metacopy000"
    agent_type = "developer"
    description = "Implement payment flow"

    subagents_dir = tmp_path / "projects" / project_hash / parent_uuid / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = subagents_dir / f"{agent_id}.jsonl"
    _write_session(jsonl_path, _minimal_records(agent_id))
    meta_path = subagents_dir / f"{agent_id}.meta.json"
    _write_meta(meta_path, agent_type, description)

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    result = ingest_subagent_session(
        jsonl_path=jsonl_path,
        bronze_dir=bronze_dir,
        manifest_path=manifest_path,
        manifest=[],
        dry_run=False,
    )

    assert result is True
    entries = read_manifest(str(manifest_path))
    assert len(entries) == 1
    entry = entries[0]

    # meta_path must be set and point to an existing file in bronze/claude-code/.
    assert entry["meta_path"] is not None
    bronze_meta = Path(entry["meta_path"])
    assert bronze_meta.exists(), f"meta.json not copied to bronze: {bronze_meta}"
    assert f"/{HARNESS}/" in str(bronze_meta)
    assert bronze_meta.suffix == ".json"
    assert bronze_meta.stem.endswith(".meta")

    # Verify the content is identical to source.
    assert bronze_meta.read_text() == meta_path.read_text()


def test_ingest_subagent_meta_missing_no_copy(tmp_path):
    """When source .meta.json is absent, no meta file appears in bronze and meta_path is null."""
    project_hash = "-Users-mu-proj-nometa"
    parent_uuid = "cccccccc-4444-5555-6666-dddddddddddd"
    agent_id = "agent-nometa00000"

    subagents_dir = tmp_path / "projects" / project_hash / parent_uuid / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = subagents_dir / f"{agent_id}.jsonl"
    _write_session(jsonl_path, _minimal_records(agent_id))
    # Deliberately no .meta.json

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    result = ingest_subagent_session(
        jsonl_path=jsonl_path,
        bronze_dir=bronze_dir,
        manifest_path=manifest_path,
        manifest=[],
        dry_run=False,
    )

    assert result is True
    entries = read_manifest(str(manifest_path))
    assert len(entries) == 1
    entry = entries[0]

    # meta_path must be null when source had no .meta.json.
    assert entry["meta_path"] is None

    # No .meta.json files should exist in the harness bronze dir.
    harness_bronze_dir = bronze_dir / HARNESS
    meta_files = list(harness_bronze_dir.glob("*.meta.json"))
    assert meta_files == [], f"Unexpected meta files in bronze: {meta_files}"


def test_ingest_top_level_meta_path_null(tmp_path):
    """Top-level sessions always have meta_path=null in their manifest entry."""
    src_dir = tmp_path / "projects" / "-Users-mu-proj"
    session_id = "top-level-session-001"
    src_file = src_dir / f"{session_id}.jsonl"
    _write_session(src_file, _minimal_records(session_id))

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    ingest_session(
        jsonl_path=src_file,
        bronze_dir=bronze_dir,
        manifest_path=manifest_path,
        manifest=[],
        dry_run=False,
    )

    entries = read_manifest(str(manifest_path))
    assert len(entries) == 1
    assert entries[0]["meta_path"] is None


def test_ingest_bronze_paths_use_harness_subdir(tmp_path):
    """All bronze_path values written by fresh ingest contain the harness subdir segment."""
    source = tmp_path / "projects"
    bronze = tmp_path / "bronze"
    manifest = tmp_path / "manifest.jsonl"

    project_hash = "-Users-mu-proj-harness"
    parent_uuid = "eeeeeeee-7777-8888-9999-ffffffffffff"
    top_session_id = "top-harness-session"
    agent_id = "agent-harness111111"

    # Top-level session.
    top_jsonl = source / project_hash / f"{top_session_id}.jsonl"
    _write_session(top_jsonl, _minimal_records(top_session_id))

    # Subagent session.
    _build_subagent_tree(
        source, project_hash, parent_uuid,
        [(agent_id, "tester", "Run tests", _minimal_records(agent_id))],
    )

    main(["--source", str(source), "--bronze-dir", str(bronze), "--manifest", str(manifest)])

    entries = read_manifest(str(manifest))
    assert len(entries) == 2

    expected_segment = f"/{HARNESS}/"
    for entry in entries:
        bp = entry["bronze_path"]
        assert expected_segment in bp, (
            f"bronze_path does not contain harness segment '{expected_segment}': {bp}"
        )


def test_ingest_subagent_idempotent_meta_copy(tmp_path):
    """Re-running ingest does not duplicate the meta.json copy or produce extra manifest entries."""
    source = tmp_path / "projects"
    bronze = tmp_path / "bronze"
    manifest = tmp_path / "manifest.jsonl"

    project_hash = "-Users-mu-proj-idem2"
    parent_uuid = "12121212-3434-5656-7878-909090909090"
    agent_id = "agent-idem2-meta0000"

    _build_subagent_tree(
        source, project_hash, parent_uuid,
        [(agent_id, "tech-lead", "Review code", _minimal_records(agent_id))],
    )

    # First run.
    main(["--source", str(source), "--bronze-dir", str(bronze), "--manifest", str(manifest)])

    # Second run -- should be a full no-op for this session.
    main(["--source", str(source), "--bronze-dir", str(bronze), "--manifest", str(manifest)])

    entries = read_manifest(str(manifest))
    agent_entries = [e for e in entries if e["session_id"] == agent_id]
    assert len(agent_entries) == 1, "Subagent manifest entry was duplicated on second run"

    # Only one meta.json should exist in bronze.
    harness_bronze_dir = bronze / HARNESS
    meta_files = list(harness_bronze_dir.glob("*.meta.json"))
    assert len(meta_files) == 1, f"Expected 1 meta file, found {len(meta_files)}: {meta_files}"


# ---------------------------------------------------------------------------
# Chunking: _split_jsonl_user_aligned
# ---------------------------------------------------------------------------

from scripts.ingest import (
    CHUNK_HARD_BYTES,
    CHUNK_SOFT_BYTES,
    _split_jsonl_user_aligned,
)


def _user_record(idx: int, payload: str = "u") -> dict:
    return {"type": "user", "uuid": f"u{idx}", "message": {"role": "user", "content": payload}}


def _assistant_record(idx: int, payload: str = "a") -> dict:
    return {"type": "assistant", "uuid": f"a{idx}", "message": {"role": "assistant", "content": payload}}


def test_split_below_soft_returns_single_chunk(tmp_path):
    """File under soft cap returns a single chunk."""
    f = tmp_path / "tiny.jsonl"
    _write_session(f, _minimal_records("tiny", n_messages=4))
    chunks = _split_jsonl_user_aligned(f)
    assert len(chunks) == 1
    # Concatenation reproduces file bytes.
    assert b"".join(chunks[0]) == f.read_bytes()


def test_split_cuts_at_user_turn_boundary(tmp_path):
    """Splitter cuts on user-turn boundaries once soft cap is exceeded."""
    f = tmp_path / "session.jsonl"
    # Build records: pad assistant messages so each turn is ~80 KB.
    big_payload = "x" * 80_000
    records = []
    for i in range(8):
        records.append(_user_record(i, payload=f"q{i}"))
        records.append(_assistant_record(i, payload=big_payload))
    _write_session(f, records)
    chunks = _split_jsonl_user_aligned(f, soft_bytes=200_000, hard_bytes=350_000)

    # Reconstruction is byte-exact.
    assert b"".join(b"".join(c) for c in chunks) == f.read_bytes()
    # Multiple chunks.
    assert len(chunks) >= 2
    # Every chunk after the first starts on a user-turn line (except possibly
    # forced byte-aligned cuts; here turns are 80KB so user-aligned wins).
    for c in chunks[1:]:
        first = json.loads(c[0])
        assert first.get("type") == "user", f"chunk did not start on user turn: {first}"


def test_split_hard_cap_forces_cut(tmp_path):
    """A single oversized turn forces a byte-aligned cut at hard cap."""
    f = tmp_path / "huge_turn.jsonl"
    # One user, then a giant assistant blob that alone exceeds hard_bytes.
    big_payload = "x" * 400_000
    records = [
        _user_record(0, payload="start"),
        _assistant_record(0, payload=big_payload),
        _user_record(1, payload="next"),
        _assistant_record(1, payload="ack"),
    ]
    _write_session(f, records)
    chunks = _split_jsonl_user_aligned(f, soft_bytes=100_000, hard_bytes=200_000)

    # We don't slice mid-line: every chunk concatenated = original bytes.
    assert b"".join(b"".join(c) for c in chunks) == f.read_bytes()
    # The giant assistant line must appear intact in exactly one chunk.
    found = 0
    for c in chunks:
        for line in c:
            if b'"a0"' in line:
                found += 1
    assert found == 1


# ---------------------------------------------------------------------------
# Chunking: ingest_session top-level
# ---------------------------------------------------------------------------

def _build_chunkable_records(session_id: str, total_bytes: int, turn_bytes: int = 60_000) -> list[dict]:
    """Build records that exceed total_bytes when serialised."""
    records = [{"type": "permission-mode", "permissionMode": "acceptEdits", "sessionId": session_id}]
    payload = "x" * turn_bytes
    i = 0
    while True:
        records.append(_user_record(i, payload=f"q{i}"))
        records.append(_assistant_record(i, payload=payload))
        i += 1
        # Approximate size check (each turn ~ turn_bytes + small overhead).
        if i * (turn_bytes + 200) > total_bytes:
            break
    return records


def test_ingest_session_chunked(tmp_path):
    """A session over CHUNK_SOFT_BYTES produces N part entries with proper schema."""
    src_dir = tmp_path / "projects" / "-Users-mu-proj"
    session_id = "big-session-001"
    src_file = src_dir / f"{session_id}.jsonl"
    _write_session(src_file, _build_chunkable_records(session_id, total_bytes=900_000))

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    result = ingest_session(
        jsonl_path=src_file,
        bronze_dir=bronze_dir,
        manifest_path=manifest_path,
        manifest=[],
        dry_run=False,
    )
    assert result is True

    entries = read_manifest(str(manifest_path))
    assert len(entries) >= 2, f"expected multiple parts, got {len(entries)}"
    total = entries[0]["total_parts"]
    assert total == len(entries)
    # Part schema invariants.
    seen_indices = set()
    harness_bronze_dir = bronze_dir / HARNESS
    for e in entries:
        assert e["session_id"].startswith(f"{session_id}_part_")
        assert e["status"] == "bronze"
        assert e["parent_session"] == session_id
        assert e["total_parts"] == total
        assert e["part_index"] >= 1
        assert e["part_index"] <= total
        seen_indices.add(e["part_index"])
        # Bronze part file exists.
        bp = Path(e["bronze_path"])
        assert bp.exists()
        assert bp.parent == harness_bronze_dir
        assert "_part_" in bp.name
    # Indices are 1..N exactly once each.
    assert seen_indices == set(range(1, total + 1))
    # Reconstruction: concatenated parts == original source.
    parts_concat = b""
    for e in sorted(entries, key=lambda x: x["part_index"]):
        parts_concat += Path(e["bronze_path"]).read_bytes()
    assert parts_concat == src_file.read_bytes()


def test_ingest_session_chunked_idempotent_re_run(tmp_path):
    """Re-ingesting an already-chunked session is a no-op."""
    src_dir = tmp_path / "projects" / "proj"
    session_id = "big-idempotent"
    src_file = src_dir / f"{session_id}.jsonl"
    _write_session(src_file, _build_chunkable_records(session_id, total_bytes=600_000))

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    # First run.
    ingest_session(src_file, bronze_dir, manifest_path, manifest=[], dry_run=False)
    first = read_manifest(str(manifest_path))

    # Second run with the manifest read back from disk -- must short-circuit.
    result = ingest_session(src_file, bronze_dir, manifest_path, manifest=first, dry_run=False)
    assert result is False
    second = read_manifest(str(manifest_path))
    assert len(second) == len(first)


def test_ingest_session_above_10mb_still_skipped(tmp_path):
    """Sessions over the new 10 MB threshold are still skipped-large."""
    src_dir = tmp_path / "projects" / "proj"
    session_id = "monster-session"
    src_file = src_dir / f"{session_id}.jsonl"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_bytes(b"x" * (LARGE_SESSION_BYTES_THRESHOLD + 1))

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    result = ingest_session(src_file, bronze_dir, manifest_path, manifest=[], dry_run=False)
    assert result is True
    entries = read_manifest(str(manifest_path))
    assert len(entries) == 1
    assert entries[0]["status"] == "skipped-large"
    assert entries[0]["part_index"] is None
    assert entries[0]["total_parts"] is None


def test_ingest_session_unsplit_has_null_part_fields(tmp_path):
    """Small sessions have part_index/total_parts = None."""
    src_dir = tmp_path / "projects" / "proj"
    session_id = "small-session"
    src_file = src_dir / f"{session_id}.jsonl"
    _write_session(src_file, _minimal_records(session_id))

    bronze_dir = tmp_path / "bronze"
    manifest_path = tmp_path / "manifest.jsonl"

    ingest_session(src_file, bronze_dir, manifest_path, manifest=[], dry_run=False)
    entries = read_manifest(str(manifest_path))
    assert entries[0]["part_index"] is None
    assert entries[0]["total_parts"] is None


def test_ingest_subagent_chunked(tmp_path):
    """A subagent session over CHUNK_SOFT_BYTES splits into parts."""
    source = tmp_path / "projects"
    project_hash = "-Users-mu-proj"
    parent_uuid = "11111111-2222-3333-4444-555555555555"
    agent_id = "agent-bigsubagent"

    big_records = _build_chunkable_records(agent_id, total_bytes=600_000)
    _build_subagent_tree(
        source, project_hash, parent_uuid,
        [(agent_id, "developer", "Long task", big_records)],
    )

    bronze = tmp_path / "bronze"
    manifest = tmp_path / "manifest.jsonl"
    main(["--source", str(source), "--bronze-dir", str(bronze), "--manifest", str(manifest)])

    entries = read_manifest(str(manifest))
    parts = [e for e in entries if e["session_id"].startswith(f"{agent_id}_part_")]
    assert len(parts) >= 2
    total = parts[0]["total_parts"]
    assert total == len(parts)
    for e in parts:
        assert e["agent"] == "developer"
        assert e["agent_task"] == "Long task"
        assert e["parent_session"] == parent_uuid
        assert e["meta_path"] is not None
        assert Path(e["meta_path"]).exists()
        assert e["part_index"] >= 1


# ---------------------------------------------------------------------------
# S1.4: Balanced splitter invariants
# ---------------------------------------------------------------------------

def test_split_balanced_no_tiny_tail(tmp_path):
    """Balanced splitter does not produce tail parts smaller than the rest."""
    f = tmp_path / "session.jsonl"
    big_payload = "x" * 80_000
    records = []
    for i in range(10):
        records.append(_user_record(i, payload=f"q{i}"))
        records.append(_assistant_record(i, payload=big_payload))
    _write_session(f, records)
    chunks = _split_jsonl_user_aligned(f, soft_bytes=200_000)

    sizes = [sum(len(b) for b in c) for c in chunks]
    assert min(sizes) > 50_000, f"tiny tail produced: {sizes}"
    # Final chunk should not be a small fraction of mean.
    assert sizes[-1] >= 0.3 * (sum(sizes) / len(sizes)), \
        f"last part is too small: sizes={sizes}"


def test_split_balanced_byte_uniform_within_window(tmp_path):
    """Heavy-line regions don't produce outsized parts (byte-based ideals)."""
    f = tmp_path / "session.jsonl"
    # Mix: 5 small turns, 7 medium-heavy turns, 5 small turns.
    # Heavy turns are 80 KB (splittable at user boundaries between them);
    # contrast with synthetic 200 KB single lines which are unsplittable
    # by a line-aligned splitter.
    records = [{"type": "permission-mode", "permissionMode": "default"}]
    for i in range(5):
        records.append(_user_record(i, payload=f"q{i}"))
        records.append(_assistant_record(i, payload="a" * 30_000))
    for i in range(5, 12):
        records.append(_user_record(i, payload=f"q{i}"))
        records.append(_assistant_record(i, payload="a" * 80_000))
    for i in range(12, 17):
        records.append(_user_record(i, payload=f"q{i}"))
        records.append(_assistant_record(i, payload="a" * 30_000))
    _write_session(f, records)
    chunks = _split_jsonl_user_aligned(f, soft_bytes=250_000)

    sizes = [sum(len(b) for b in c) for c in chunks]
    avg = sum(sizes) / len(sizes)
    # No part should exceed 1.6x the mean -- byte-based placement adapts to
    # local density so the heavy region gets its own parts.
    max_size = max(sizes)
    assert max_size <= 1.6 * avg, \
        f"part size variance too high: max={max_size}, avg={avg}, sizes={sizes}"


def test_split_balanced_monotonic_cuts(tmp_path):
    """Cut indices are strictly increasing -- no overlap or empty parts."""
    f = tmp_path / "session.jsonl"
    big_payload = "x" * 50_000
    records = []
    for i in range(12):
        records.append(_user_record(i, payload=f"q{i}"))
        records.append(_assistant_record(i, payload=big_payload))
    _write_session(f, records)
    chunks = _split_jsonl_user_aligned(f, soft_bytes=200_000)
    # No empty chunks.
    for c in chunks:
        assert len(c) > 0, "empty chunk produced"
    # Reconstruction byte-exact.
    assert b"".join(b"".join(c) for c in chunks) == f.read_bytes()


def test_split_balanced_target_parts_matches_size(tmp_path):
    """target_parts = ceil(total / soft) -- verify part count for known size."""
    f = tmp_path / "session.jsonl"
    # Build ~750 KB session -> ceil(750/250) = 3 parts.
    records = []
    for i in range(12):
        records.append(_user_record(i, payload=f"q{i}"))
        records.append(_assistant_record(i, payload="x" * 60_000))
    _write_session(f, records)
    total_size = f.stat().st_size
    chunks = _split_jsonl_user_aligned(f, soft_bytes=250_000)
    expected = (total_size + 250_000 - 1) // 250_000
    assert len(chunks) == expected, \
        f"expected {expected} parts for {total_size} bytes, got {len(chunks)}"


# ---------------------------------------------------------------------------
# MVP-2: Sidecar gate (Issue 2), project_id/thread_id (Issue 3), cutoff (Issue 5)
# ---------------------------------------------------------------------------

from scripts.ingest import load_sidecar_index, load_ingest_cutoff, DEFAULT_INGEST_FROM


def _write_sidecar(path: Path, records: list[dict]) -> None:
    """Write sidecar records as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _minimal_sidecar_record(session_id: str, started_at: str = "2026-04-28T10:00:00+00:00",
                              project_id: str = "abc123", thread_id: str = "tid999") -> dict:
    return {
        "session_id": session_id,
        "started_at": started_at,
        "project_id": project_id,
        "branch": "main",
        "thread_id": thread_id,
        "cwd": "/tmp/project",
        "hook_version": 1,
    }


class TestLoadSidecarIndex:
    def test_absent_file_returns_empty(self, tmp_path):
        idx = load_sidecar_index(tmp_path / "no_such.jsonl")
        assert idx == {}

    def test_single_record_indexed_by_session_id(self, tmp_path):
        p = tmp_path / "sessions.jsonl"
        rec = _minimal_sidecar_record("sess-aaa")
        _write_sidecar(p, [rec])
        idx = load_sidecar_index(p)
        assert "sess-aaa" in idx
        assert idx["sess-aaa"]["project_id"] == "abc123"

    def test_last_write_wins_for_duplicate_session_id(self, tmp_path):
        p = tmp_path / "sessions.jsonl"
        _write_sidecar(p, [
            _minimal_sidecar_record("sess-dup", project_id="first"),
            _minimal_sidecar_record("sess-dup", project_id="second"),
        ])
        idx = load_sidecar_index(p)
        assert idx["sess-dup"]["project_id"] == "second"


class TestSidecarGate:
    """Issue 2: ingest_session and ingest_subagent_session reject unsidecarred sessions."""

    def test_no_sidecar_session_rejected(self, tmp_path):
        src_dir = tmp_path / "projects" / "-Users-mu"
        session_id = "sess-no-sidecar"
        src_file = src_dir / f"{session_id}.jsonl"
        _write_session(src_file, _minimal_records(session_id))

        result = ingest_session(
            jsonl_path=src_file,
            bronze_dir=tmp_path / "bronze",
            manifest_path=tmp_path / "manifest.jsonl",
            manifest=[],
            dry_run=False,
            sidecar_index={},  # empty -- no entry for this session
        )
        assert result is False
        # Not written to manifest
        manifest_path = tmp_path / "manifest.jsonl"
        assert not manifest_path.exists() or manifest_path.read_text().strip() == ""

    def test_matching_sidecar_session_admitted(self, tmp_path):
        src_dir = tmp_path / "projects" / "-Users-mu"
        session_id = "sess-with-sidecar"
        src_file = src_dir / f"{session_id}.jsonl"
        _write_session(src_file, _minimal_records(session_id))

        sidecar_record = _minimal_sidecar_record(session_id)
        sidecar_index = {session_id: sidecar_record}

        result = ingest_session(
            jsonl_path=src_file,
            bronze_dir=tmp_path / "bronze",
            manifest_path=tmp_path / "manifest.jsonl",
            manifest=[],
            dry_run=False,
            sidecar_index=sidecar_index,
        )
        assert result is True

    def test_no_sidecar_index_bypasses_gate(self, tmp_path):
        """Passing sidecar_index=None disables the gate (backward compat)."""
        src_dir = tmp_path / "projects" / "-Users-mu"
        session_id = "sess-no-gate"
        src_file = src_dir / f"{session_id}.jsonl"
        _write_session(src_file, _minimal_records(session_id))

        result = ingest_session(
            jsonl_path=src_file,
            bronze_dir=tmp_path / "bronze",
            manifest_path=tmp_path / "manifest.jsonl",
            manifest=[],
            dry_run=False,
            sidecar_index=None,  # gate disabled
        )
        assert result is True


class TestProjectIdAndThreadId:
    """Issue 3: project_id and thread_id from sidecar propagate to manifest row."""

    def test_project_id_and_thread_id_in_manifest(self, tmp_path):
        src_dir = tmp_path / "projects" / "-Users-mu"
        session_id = "sess-proj-thread"
        src_file = src_dir / f"{session_id}.jsonl"
        _write_session(src_file, _minimal_records(session_id))

        sidecar_record = _minimal_sidecar_record(
            session_id, project_id="proj-abc", thread_id="thread-xyz"
        )
        sidecar_index = {session_id: sidecar_record}
        sidecar_p = tmp_path / "sessions.jsonl"
        _write_sidecar(sidecar_p, [sidecar_record])

        manifest_path = tmp_path / "manifest.jsonl"
        ingest_session(
            jsonl_path=src_file,
            bronze_dir=tmp_path / "bronze",
            manifest_path=manifest_path,
            manifest=[],
            dry_run=False,
            sidecar_index=sidecar_index,
            sidecar_path=sidecar_p,
        )

        from scripts.manifest import read_manifest
        entries = read_manifest(str(manifest_path))
        assert len(entries) == 1
        assert entries[0].get("project_id") == "proj-abc"
        assert entries[0].get("thread_id") == "thread-xyz"
        assert entries[0].get("sidecar_path") is not None


class TestIngestCutoff:
    """Issue 5: HIPPO_INGEST_FROM cutoff skips pre-cutoff sessions."""

    def test_session_before_cutoff_skipped(self, tmp_path):
        src_dir = tmp_path / "projects" / "-Users-mu"
        session_id = "sess-old"
        src_file = src_dir / f"{session_id}.jsonl"
        _write_session(src_file, _minimal_records(session_id))

        # started_at is 2026-04-27 -- before the 2026-04-28 default cutoff
        sidecar_record = _minimal_sidecar_record(session_id, started_at="2026-04-27T10:00:00+00:00")
        sidecar_index = {session_id: sidecar_record}

        result = ingest_session(
            jsonl_path=src_file,
            bronze_dir=tmp_path / "bronze",
            manifest_path=tmp_path / "manifest.jsonl",
            manifest=[],
            dry_run=False,
            sidecar_index=sidecar_index,
            ingest_cutoff="2026-04-28",
        )
        assert result is False

    def test_session_on_cutoff_day_admitted(self, tmp_path):
        src_dir = tmp_path / "projects" / "-Users-mu"
        session_id = "sess-cutoff-day"
        src_file = src_dir / f"{session_id}.jsonl"
        _write_session(src_file, _minimal_records(session_id))

        # started_at is 2026-04-28 -- on the cutoff day, should be admitted
        sidecar_record = _minimal_sidecar_record(session_id, started_at="2026-04-28T00:00:00+00:00")
        sidecar_index = {session_id: sidecar_record}

        result = ingest_session(
            jsonl_path=src_file,
            bronze_dir=tmp_path / "bronze",
            manifest_path=tmp_path / "manifest.jsonl",
            manifest=[],
            dry_run=False,
            sidecar_index=sidecar_index,
            ingest_cutoff="2026-04-28",
        )
        assert result is True

    def test_unsidecarred_session_rejected_before_cutoff_check(self, tmp_path):
        """Sessions with no sidecar must be rejected by the sidecar gate before
        the cutoff logic is reached (Issue 2 gate runs first)."""
        src_dir = tmp_path / "projects" / "-Users-mu"
        session_id = "sess-no-sidecar-cutoff"
        src_file = src_dir / f"{session_id}.jsonl"
        _write_session(src_file, _minimal_records(session_id))

        # Empty sidecar_index -- gate rejects before cutoff check
        result = ingest_session(
            jsonl_path=src_file,
            bronze_dir=tmp_path / "bronze",
            manifest_path=tmp_path / "manifest.jsonl",
            manifest=[],
            dry_run=False,
            sidecar_index={},
            ingest_cutoff="2026-04-28",
        )
        assert result is False


class TestLoadIngestCutoff:
    def test_default_when_no_config_and_no_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HIPPO_INGEST_FROM", raising=False)
        cutoff = load_ingest_cutoff(config_path=tmp_path / "no_config")
        assert cutoff == DEFAULT_INGEST_FROM

    def test_env_var_overrides_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HIPPO_INGEST_FROM", "2025-01-01")
        cutoff = load_ingest_cutoff(config_path=tmp_path / "no_config")
        assert cutoff == "2025-01-01"

    def test_config_file_read(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HIPPO_INGEST_FROM", raising=False)
        cfg = tmp_path / "config"
        cfg.write_text("[hippo]\ningest_from = 2025-06-15\n")
        cutoff = load_ingest_cutoff(config_path=cfg)
        assert cutoff == "2025-06-15"
