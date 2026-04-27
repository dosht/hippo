"""Unit tests for scripts/manifest.py."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from scripts.manifest import (
    append_manifest,
    find_entry,
    read_manifest,
    update_manifest,
)


def _make_entry(session_id: str, **overrides) -> dict:
    base = {
        "session_id": session_id,
        "source_path": f"/fake/{session_id}.jsonl",
        "bronze_path": f"/bronze/{session_id}.jsonl",
        "silver_path": None,
        "gold_paths": [],
        "status": "bronze",
        "ingested_at": "2026-04-17T00:00:00+00:00",
        "compacted_at": None,
        "extracted_at": None,
        "bronze_size_bytes": 1234,
        "silver_size_bytes": None,
        "error": None,
        "harness": "claude-code",
        "project_hash": "-Users-mu-src-hippo",
        "agent": None,
        "parent_session": None,
        "short": False,
        "memory_query": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# read_manifest
# ---------------------------------------------------------------------------

def test_read_manifest_missing_file(tmp_path):
    """read_manifest returns empty list when file does not exist."""
    result = read_manifest(str(tmp_path / "nonexistent.jsonl"))
    assert result == []


def test_read_manifest_empty_file(tmp_path):
    """read_manifest returns empty list for an empty file."""
    f = tmp_path / "manifest.jsonl"
    f.write_text("")
    assert read_manifest(str(f)) == []


def test_read_manifest_single_entry(tmp_path):
    """read_manifest parses a single JSONL line correctly."""
    f = tmp_path / "manifest.jsonl"
    entry = _make_entry("abc123")
    f.write_text(json.dumps(entry) + "\n")
    result = read_manifest(str(f))
    assert len(result) == 1
    assert result[0]["session_id"] == "abc123"


def test_read_manifest_multiple_entries(tmp_path):
    """read_manifest returns all entries in file order."""
    f = tmp_path / "manifest.jsonl"
    entries = [_make_entry(f"sess-{i}") for i in range(3)]
    f.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    result = read_manifest(str(f))
    assert [r["session_id"] for r in result] == ["sess-0", "sess-1", "sess-2"]


# ---------------------------------------------------------------------------
# append_manifest
# ---------------------------------------------------------------------------

def test_append_manifest_creates_file(tmp_path):
    """append_manifest creates the file if it does not exist."""
    f = tmp_path / "manifest.jsonl"
    entry = _make_entry("new-session")
    append_manifest(str(f), entry)
    assert f.exists()
    result = read_manifest(str(f))
    assert len(result) == 1
    assert result[0]["session_id"] == "new-session"


def test_append_manifest_does_not_overwrite(tmp_path):
    """append_manifest does not truncate existing content."""
    f = tmp_path / "manifest.jsonl"
    e1 = _make_entry("session-1")
    e2 = _make_entry("session-2")
    append_manifest(str(f), e1)
    append_manifest(str(f), e2)
    result = read_manifest(str(f))
    assert len(result) == 2
    assert {r["session_id"] for r in result} == {"session-1", "session-2"}


# ---------------------------------------------------------------------------
# update_manifest
# ---------------------------------------------------------------------------

def test_update_manifest_updates_field(tmp_path):
    """update_manifest changes the specified field on the matching entry."""
    f = tmp_path / "manifest.jsonl"
    entry = _make_entry("update-me", status="bronze")
    append_manifest(str(f), entry)
    update_manifest(str(f), "update-me", {"status": "silver"})
    result = read_manifest(str(f))
    assert result[0]["status"] == "silver"


def test_update_manifest_preserves_other_entries(tmp_path):
    """update_manifest only changes the targeted entry."""
    f = tmp_path / "manifest.jsonl"
    append_manifest(str(f), _make_entry("alpha", status="bronze"))
    append_manifest(str(f), _make_entry("beta", status="bronze"))
    update_manifest(str(f), "alpha", {"status": "silver"})
    result = read_manifest(str(f))
    by_id = {e["session_id"]: e for e in result}
    assert by_id["alpha"]["status"] == "silver"
    assert by_id["beta"]["status"] == "bronze"


def test_update_manifest_missing_session_raises(tmp_path):
    """update_manifest raises KeyError when session_id is not found."""
    f = tmp_path / "manifest.jsonl"
    append_manifest(str(f), _make_entry("existing"))
    with pytest.raises(KeyError):
        update_manifest(str(f), "does-not-exist", {"status": "silver"})


# ---------------------------------------------------------------------------
# find_entry
# ---------------------------------------------------------------------------

def test_find_entry_found():
    """find_entry returns the matching entry."""
    entries = [_make_entry("a"), _make_entry("b"), _make_entry("c")]
    result = find_entry(entries, "b")
    assert result is not None
    assert result["session_id"] == "b"


def test_find_entry_not_found():
    """find_entry returns None when session_id is absent."""
    entries = [_make_entry("x"), _make_entry("y")]
    assert find_entry(entries, "z") is None


def test_find_entry_empty_list():
    """find_entry returns None on an empty manifest."""
    assert find_entry([], "anything") is None
