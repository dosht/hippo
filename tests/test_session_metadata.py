"""Tests for MVP-1-14: Session Metadata Enrichment.

Covers:
  - extract_session_metadata() helper (ingest.py)
  - _silver_frontmatter() new fields (compact.py)
  - Round-trip: ingest -> compact frontmatter -> extract prompt injection
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.compact import _silver_frontmatter
from scripts.ingest import extract_session_metadata, ingest_session, main
from scripts.manifest import read_manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """Write a list of record dicts as JSONL to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _make_session_records(
    session_id: str,
    cwd: str = "/Users/mu/src/myproject",
    git_branch: str = "main",
    timestamp: str = "2026-04-18T10:00:00.000Z",
    n_messages: int = 4,
) -> list[dict]:
    """Produce a realistic list of JSONL records for a session with metadata."""
    records = [
        {
            "type": "permission-mode",
            "permissionMode": "acceptEdits",
            "sessionId": session_id,
            "cwd": cwd,
            "gitBranch": git_branch,
        },
    ]
    for i in range(n_messages):
        role = "user" if i % 2 == 0 else "assistant"
        records.append({
            "type": role,
            "uuid": f"msg-{i}",
            "sessionId": session_id,
            "cwd": cwd,
            "gitBranch": git_branch,
            "timestamp": timestamp,
            "message": {"role": role, "content": f"message {i}"},
        })
    return records


# ---------------------------------------------------------------------------
# extract_session_metadata -- all fields present
# ---------------------------------------------------------------------------


class TestExtractSessionMetadataAllPresent:
    def test_returns_cwd(self, tmp_path):
        """Returns cwd from the first record that has it."""
        f = tmp_path / "session.jsonl"
        _write_jsonl(f, _make_session_records("s1", cwd="/Users/mu/src/myapp"))
        result = extract_session_metadata(f)
        assert result["cwd"] == "/Users/mu/src/myapp"

    def test_returns_git_branch(self, tmp_path):
        """Returns git_branch from the first record that has gitBranch."""
        f = tmp_path / "session.jsonl"
        _write_jsonl(f, _make_session_records("s1", git_branch="feature/payments"))
        result = extract_session_metadata(f)
        assert result["git_branch"] == "feature/payments"

    def test_returns_session_started_at(self, tmp_path):
        """Returns session_started_at from first non-permission-mode record."""
        f = tmp_path / "session.jsonl"
        ts = "2026-04-18T10:00:00.000Z"
        _write_jsonl(f, _make_session_records("s1", timestamp=ts))
        result = extract_session_metadata(f)
        assert result["session_started_at"] == ts

    def test_permission_mode_timestamp_excluded(self, tmp_path):
        """Timestamp from permission-mode records must NOT be used."""
        f = tmp_path / "session.jsonl"
        records = [
            {
                "type": "permission-mode",
                "sessionId": "s1",
                "timestamp": "2000-01-01T00:00:00.000Z",  # should be skipped
                "cwd": "/some/path",
                "gitBranch": "main",
            },
            {
                "type": "user",
                "sessionId": "s1",
                "timestamp": "2026-04-18T10:00:00.000Z",  # this is the real start
                "cwd": "/some/path",
                "gitBranch": "main",
                "message": {"role": "user", "content": "hello"},
            },
        ]
        _write_jsonl(f, records)
        result = extract_session_metadata(f)
        # Must use the user record's timestamp, not the permission-mode one.
        assert result["session_started_at"] == "2026-04-18T10:00:00.000Z"

    def test_cwd_from_permission_mode_is_ok(self, tmp_path):
        """cwd may come from permission-mode records (it's still the correct cwd)."""
        f = tmp_path / "session.jsonl"
        records = [
            {
                "type": "permission-mode",
                "sessionId": "s1",
                "cwd": "/from-perm-mode",
                "gitBranch": "feat",
            },
        ]
        _write_jsonl(f, records)
        result = extract_session_metadata(f)
        assert result["cwd"] == "/from-perm-mode"
        assert result["git_branch"] == "feat"


# ---------------------------------------------------------------------------
# extract_session_metadata -- missing fields
# ---------------------------------------------------------------------------


class TestExtractSessionMetadataMissingFields:
    def test_no_cwd_returns_none(self, tmp_path):
        """Returns cwd=None when no record carries the cwd field."""
        f = tmp_path / "session.jsonl"
        records = [{"type": "user", "sessionId": "s1", "message": {"role": "user", "content": "hi"}}]
        _write_jsonl(f, records)
        result = extract_session_metadata(f)
        assert result["cwd"] is None

    def test_no_git_branch_returns_none(self, tmp_path):
        """Returns git_branch=None when no record carries gitBranch."""
        f = tmp_path / "session.jsonl"
        records = [{"type": "user", "sessionId": "s1", "cwd": "/some/path"}]
        _write_jsonl(f, records)
        result = extract_session_metadata(f)
        assert result["git_branch"] is None

    def test_no_timestamp_returns_none(self, tmp_path):
        """Returns session_started_at=None when no non-permission-mode record has timestamp."""
        f = tmp_path / "session.jsonl"
        records = [{"type": "user", "sessionId": "s1", "cwd": "/p", "gitBranch": "main"}]
        _write_jsonl(f, records)
        result = extract_session_metadata(f)
        assert result["session_started_at"] is None

    def test_only_permission_mode_returns_all_null_timestamps(self, tmp_path):
        """A file with only a permission-mode record returns session_started_at=None."""
        f = tmp_path / "session.jsonl"
        records = [
            {
                "type": "permission-mode",
                "sessionId": "s1",
                "cwd": "/some/path",
                "gitBranch": "main",
                "timestamp": "2026-01-01T00:00:00.000Z",
            },
        ]
        _write_jsonl(f, records)
        result = extract_session_metadata(f)
        # cwd and git_branch may come from permission-mode; session_started_at must not.
        assert result["cwd"] == "/some/path"
        assert result["git_branch"] == "main"
        assert result["session_started_at"] is None

    def test_empty_values_treated_as_none(self, tmp_path):
        """Records with empty string for cwd or gitBranch are treated as absent."""
        f = tmp_path / "session.jsonl"
        records = [
            {"type": "user", "sessionId": "s1", "cwd": "", "gitBranch": ""},
        ]
        _write_jsonl(f, records)
        result = extract_session_metadata(f)
        assert result["cwd"] is None
        assert result["git_branch"] is None


# ---------------------------------------------------------------------------
# extract_session_metadata -- malformed JSON lines
# ---------------------------------------------------------------------------


class TestExtractSessionMetadataMalformed:
    def test_malformed_lines_skipped_gracefully(self, tmp_path):
        """Malformed JSON lines are skipped; valid lines are still parsed."""
        f = tmp_path / "session.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "this is not json\n",
            "{broken json\n",
            json.dumps({
                "type": "user",
                "cwd": "/my/project",
                "gitBranch": "main",
                "timestamp": "2026-04-18T10:00:00.000Z",
            }) + "\n",
        ]
        f.write_text("".join(lines), encoding="utf-8")
        result = extract_session_metadata(f)
        assert result["cwd"] == "/my/project"
        assert result["git_branch"] == "main"
        assert result["session_started_at"] == "2026-04-18T10:00:00.000Z"

    def test_missing_file_returns_all_none(self):
        """Returns all-None dict gracefully when file does not exist."""
        result = extract_session_metadata(Path("/nonexistent/session.jsonl"))
        assert result == {"cwd": None, "git_branch": None, "session_started_at": None, "project": None}

    def test_all_malformed_returns_all_none(self, tmp_path):
        """Returns all-None when every line is malformed JSON."""
        f = tmp_path / "session.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("not json\nalso not json\n!!!\n", encoding="utf-8")
        result = extract_session_metadata(f)
        assert result == {"cwd": None, "git_branch": None, "session_started_at": None, "project": None}

    def test_stops_after_20_non_permission_records(self, tmp_path):
        """Reads at most 20 non-permission-mode records then stops."""
        f = tmp_path / "session.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        # Write 25 records without the fields we want, then one with them.
        records = []
        for i in range(25):
            records.append({"type": "user", "uuid": f"m{i}"})
        records.append({
            "type": "user",
            "cwd": "/late/cwd",
            "gitBranch": "late-branch",
            "timestamp": "2026-04-18T10:00:00.000Z",
        })
        _write_jsonl(f, records)
        result = extract_session_metadata(f)
        # The late record is beyond the 20-record budget; fields must be None.
        assert result["cwd"] is None
        assert result["git_branch"] is None
        assert result["session_started_at"] is None


# ---------------------------------------------------------------------------
# ingest_session -- new manifest fields present
# ---------------------------------------------------------------------------


class TestIngestSessionNewFields:
    def test_new_fields_written_to_manifest(self, tmp_path):
        """ingest_session writes cwd, git_branch, session_started_at to manifest."""
        src_dir = tmp_path / "projects" / "-Users-mu-src-myproject"
        session_id = "sess-meta-001"
        src_file = src_dir / f"{session_id}.jsonl"
        records = _make_session_records(
            session_id,
            cwd="/Users/mu/src/myproject",
            git_branch="feature/pay",
            timestamp="2026-04-18T10:00:00.000Z",
        )
        _write_jsonl(src_file, records)

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
        entry = entries[0]
        assert entry["cwd"] == "/Users/mu/src/myproject"
        assert entry["git_branch"] == "feature/pay"
        assert entry["session_started_at"] == "2026-04-18T10:00:00.000Z"

    def test_new_fields_null_when_absent(self, tmp_path):
        """ingest_session writes null for cwd/git_branch/session_started_at when not in JSONL."""
        src_dir = tmp_path / "projects" / "proj"
        session_id = "sess-no-meta"
        src_file = src_dir / f"{session_id}.jsonl"
        records = [
            {"type": "permission-mode", "sessionId": session_id},
            {"type": "user", "message": {"role": "user", "content": "hello"}},
        ]
        _write_jsonl(src_file, records)

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
        entry = entries[0]
        assert entry["cwd"] is None
        assert entry["git_branch"] is None
        assert entry["session_started_at"] is None

    def test_all_schema_fields_present_including_new(self, tmp_path):
        """Manifest entry produced after Phase A includes all three new fields."""
        source = tmp_path / "projects"
        bronze = tmp_path / "bronze"
        manifest = tmp_path / "manifest.jsonl"

        session_id = "full-schema-check"
        f = source / "-proj" / f"{session_id}.jsonl"
        _write_jsonl(f, _make_session_records(session_id))

        main(["--source", str(source), "--bronze-dir", str(bronze), "--manifest", str(manifest)])

        entries = read_manifest(str(manifest))
        assert len(entries) == 1
        entry = entries[0]

        new_fields = {"cwd", "git_branch", "session_started_at"}
        missing = new_fields - set(entry.keys())
        assert missing == set(), f"Missing new manifest fields: {missing}"


# ---------------------------------------------------------------------------
# _silver_frontmatter -- new fields present
# ---------------------------------------------------------------------------


class TestSilverFrontmatterNewFields:
    def _make_entry(self, **kwargs) -> dict:
        base = {
            "session_id": "sess-fm-001",
            "project_hash": "-Users-mu-src-myapp",
            "agent": None,
            "parent_session": None,
            "ingested_at": "2026-04-18T10:00:00+00:00",
            "harness": "claude-code",
        }
        base.update(kwargs)
        return base

    def test_cwd_present_in_frontmatter(self):
        """cwd from manifest entry appears in silver frontmatter."""
        entry = self._make_entry(cwd="/Users/mu/src/myapp")
        result = _silver_frontmatter(entry)
        assert "cwd: /Users/mu/src/myapp" in result

    def test_git_branch_present_in_frontmatter(self):
        """git_branch from manifest entry appears in silver frontmatter."""
        entry = self._make_entry(git_branch="feature/payments")
        result = _silver_frontmatter(entry)
        assert "git_branch: feature/payments" in result

    def test_session_started_at_present_in_frontmatter(self):
        """session_started_at from manifest entry appears in silver frontmatter."""
        entry = self._make_entry(session_started_at="2026-04-18T10:00:00.000Z")
        result = _silver_frontmatter(entry)
        assert "session_started_at: 2026-04-18T10:00:00.000Z" in result

    def test_null_cwd_serialized_as_null(self):
        """cwd=None serializes as 'null' in frontmatter."""
        entry = self._make_entry(cwd=None)
        result = _silver_frontmatter(entry)
        assert "cwd: null" in result

    def test_null_git_branch_serialized_as_null(self):
        """git_branch=None serializes as 'null' in frontmatter."""
        entry = self._make_entry(git_branch=None)
        result = _silver_frontmatter(entry)
        assert "git_branch: null" in result

    def test_null_session_started_at_serialized_as_null(self):
        """session_started_at=None serializes as 'null' in frontmatter."""
        entry = self._make_entry(session_started_at=None)
        result = _silver_frontmatter(entry)
        assert "session_started_at: null" in result

    def test_missing_keys_default_to_null(self):
        """Old manifest entries without the new keys produce null values gracefully."""
        # Simulate an old entry that pre-dates Phase A (no cwd/git_branch/session_started_at).
        entry = {
            "session_id": "old-sess",
            "project_hash": "-Users-mu-src-old",
            "agent": None,
            "parent_session": None,
            "ingested_at": "2025-01-01T00:00:00+00:00",
            "harness": "claude-code",
            # No cwd, git_branch, or session_started_at keys.
        }
        result = _silver_frontmatter(entry)
        assert "cwd: null" in result
        assert "git_branch: null" in result
        assert "session_started_at: null" in result

    def test_frontmatter_is_valid_yaml_structure(self):
        """Frontmatter starts and ends with --- delimiters with a trailing blank line."""
        entry = self._make_entry(
            cwd="/some/path",
            git_branch="main",
            session_started_at="2026-04-18T10:00:00.000Z",
        )
        result = _silver_frontmatter(entry)
        lines = result.split("\n")
        assert lines[0] == "---"
        # Second-to-last line should be "---", last line should be ""
        assert lines[-2] == "---"
        assert lines[-1] == ""

    def test_all_three_fields_present_together(self):
        """All three new fields appear together when all are provided."""
        entry = self._make_entry(
            cwd="/Users/mu/src/myproject",
            git_branch="payments",
            session_started_at="2026-04-18T09:00:00.000Z",
        )
        result = _silver_frontmatter(entry)
        assert "cwd: /Users/mu/src/myproject" in result
        assert "git_branch: payments" in result
        assert "session_started_at: 2026-04-18T09:00:00.000Z" in result


# ---------------------------------------------------------------------------
# extract.py metadata injection -- new fields injected
# ---------------------------------------------------------------------------


class TestExtractMetadataInjection:
    """Test that call_claude_extract injects git_branch and session_started_at."""

    def _run_extract(self, metadata: dict) -> str:
        """Run call_claude_extract with a mock subprocess and return the combined prompt."""
        from scripts.extract import call_claude_extract

        captured_cmd_input: list[str] = []

        def fake_run(cmd, **kwargs):
            # Capture the combined prompt string (last positional arg to claude -p).
            captured_cmd_input.append(cmd[-1])
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = "NO_NEW_KNOWLEDGE"
            return mock

        with patch("scripts.extract.subprocess.run", side_effect=fake_run):
            call_claude_extract(
                silver_path=Path("/fake/silver.md"),
                prompt="PROMPT",
                session_id="sess-001",
                metadata=metadata,
                silver_body="silver body text",
                dry_run=False,
            )

        assert len(captured_cmd_input) == 1
        return captured_cmd_input[0]

    def test_git_branch_injected_when_present(self):
        """git_branch is included in the combined prompt when non-null."""
        combined = self._run_extract({
            "project": "myproject",
            "agent": "developer",
            "git_branch": "feature/payments",
            "session_started_at": None,
        })
        assert "git_branch: feature/payments" in combined

    def test_session_started_at_injected_when_present(self):
        """session_started_at is included in the combined prompt when non-null."""
        combined = self._run_extract({
            "project": "myproject",
            "agent": "developer",
            "git_branch": None,
            "session_started_at": "2026-04-18T10:00:00.000Z",
        })
        assert "session_started_at: 2026-04-18T10:00:00.000Z" in combined

    def test_null_git_branch_not_injected(self):
        """git_branch=None is not included in the combined prompt."""
        combined = self._run_extract({
            "project": "myproject",
            "agent": "developer",
            "git_branch": None,
            "session_started_at": None,
        })
        assert "git_branch" not in combined

    def test_null_session_started_at_not_injected(self):
        """session_started_at=None is not included in the combined prompt."""
        combined = self._run_extract({
            "project": "myproject",
            "agent": "developer",
            "git_branch": None,
            "session_started_at": None,
        })
        assert "session_started_at" not in combined

    def test_string_null_git_branch_not_injected(self):
        """git_branch='null' (string from old silver frontmatter) is not injected."""
        combined = self._run_extract({
            "project": "myproject",
            "agent": "developer",
            "git_branch": "null",
            "session_started_at": "null",
        })
        assert "git_branch" not in combined
        assert "session_started_at" not in combined

    def test_session_id_still_injected(self):
        """session_id is always injected regardless of new fields."""
        combined = self._run_extract({
            "project": "myproject",
            "agent": "developer",
        })
        assert "session_id: sess-001" in combined

    def test_project_still_injected(self):
        """project is always injected regardless of new fields."""
        combined = self._run_extract({
            "project": "myproject",
            "agent": "developer",
        })
        assert "project: myproject" in combined


# ---------------------------------------------------------------------------
# Round-trip integration: ingest -> _silver_frontmatter -> parse_frontmatter
# ---------------------------------------------------------------------------


class TestRoundTripPropagation:
    """Integration-style: verify new fields survive ingest -> compact frontmatter -> parse."""

    def test_fields_survive_ingest_to_silver_frontmatter(self, tmp_path):
        """Fields written by ingest_session appear correctly in silver frontmatter."""
        from scripts.extract import _parse_frontmatter

        src_dir = tmp_path / "projects" / "-Users-mu-src-roundtrip"
        session_id = "round-trip-001"
        src_file = src_dir / f"{session_id}.jsonl"
        records = _make_session_records(
            session_id,
            cwd="/Users/mu/src/roundtrip",
            git_branch="rtt-branch",
            timestamp="2026-04-18T11:00:00.000Z",
        )
        _write_jsonl(src_file, records)

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
        entry = entries[0]

        # Build the silver frontmatter from the ingested entry.
        frontmatter_str = _silver_frontmatter(entry)

        # Parse it back to verify round-trip fidelity.
        meta, _ = _parse_frontmatter(frontmatter_str + "\nbody text")
        assert meta.get("cwd") == "/Users/mu/src/roundtrip"
        assert meta.get("git_branch") == "rtt-branch"
        assert meta.get("session_started_at") == "2026-04-18T11:00:00.000Z"
