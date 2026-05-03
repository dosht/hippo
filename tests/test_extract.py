"""Unit tests for scripts/extract.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from scripts.extract import (
    EXTRACTION_MODEL,
    QMD_COLLECTION,
    _parse_entry_block,
    _parse_frontmatter,
    call_claude_extract,
    extract_session,
    is_duplicate,
    is_eligible,
    is_recoverable,
    load_prompt,
    main,
    reindex_qmd,
    validate_frontmatter,
    write_gold_entry,
)
from scripts.manifest import read_manifest


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_manifest(tmp_path: Path) -> Path:
    """Return path to a temporary manifest.jsonl file (does not exist yet)."""
    return tmp_path / "manifest.jsonl"


@pytest.fixture()
def gold_dir(tmp_path: Path) -> Path:
    """Return a temporary gold directory (not created yet)."""
    return tmp_path / "gold" / "entries"


@pytest.fixture()
def silver_dir(tmp_path: Path) -> Path:
    """Return a temporary silver directory."""
    d = tmp_path / "silver"
    d.mkdir()
    return d


@pytest.fixture()
def prompt_path(tmp_path: Path) -> Path:
    """Write a minimal extract.md prompt and return its path."""
    p = tmp_path / "extract.md"
    p.write_text("# Extract Playbook\n\nExtract gold entries.\n", encoding="utf-8")
    return p


def _write_silver(silver_dir: Path, session_id: str, content: str = "silver content") -> Path:
    """Write a fake silver file and return its path."""
    p = silver_dir / f"{session_id}.md"
    p.write_text(content, encoding="utf-8")
    return p


def _write_manifest(manifest_path: Path, entries: list[dict]) -> None:
    """Write a list of manifest entries as JSONL."""
    with open(manifest_path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def _make_entry(
    session_id: str = "sess-001",
    status: str = "silver",
    silver_path: str = "",
    **kwargs,
) -> dict:
    """Return a minimal manifest entry dict."""
    return {
        "session_id": session_id,
        "source_path": f"/src/{session_id}.jsonl",
        "bronze_path": f"/bronze/{session_id}.jsonl",
        "silver_path": silver_path,
        "gold_paths": [],
        "status": status,
        "ingested_at": "2026-04-17T10:00:00+00:00",
        "compacted_at": "2026-04-17T11:00:00+00:00",
        "extracted_at": None,
        "bronze_size_bytes": 50000,
        "silver_size_bytes": 5000,
        "error": None,
        "harness": "claude-code",
        "project_hash": "-Users-mu-src-myproject",
        "agent": None,
        "parent_session": None,
        "short": False,
        "memory_query": False,
        **kwargs,
    }


def _make_valid_entry_dict(entry_id: str = "mem-test-entry") -> dict:
    """Return a dict with all required frontmatter fields plus a body."""
    return {
        "id": entry_id,
        "type": "operational",
        "topics": ["testing", "python"],
        "projects": ["all"],
        "agents": ["all"],
        "confidence": "medium",
        "source_sessions": ["sess-001"],
        "created": "2026-04-17",
        "last_validated": "2026-04-17",
        "last_queried": None,
        "query_count": 0,
        "staleness_policy": "90d",
        "supersedes": [],
        "body": "# Test Entry\n\nThis is a test entry body.",
    }


VALID_ENTRY_BLOCK = """\
---
id: mem-test-entry
type: operational
topics: [testing, python]
projects: [all]
agents: [all]
confidence: medium
source_sessions: [sess-abc-123]
created: 2026-04-17
last_validated: 2026-04-17
last_queried: null
query_count: 0
staleness_policy: 90d
supersedes: []
---

# Test Entry

This is a test entry body.
"""


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        text = "---\nkey: val\n---\nbody"
        meta, body = _parse_frontmatter(text)
        assert meta == {"key": "val"}
        assert body == "body"

    def test_no_frontmatter(self):
        text = "no frontmatter here\njust body text"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_null_value(self):
        text = "---\nkey: null\n---\nbody"
        meta, body = _parse_frontmatter(text)
        assert meta["key"] is None

    def test_list_value(self):
        text = "---\nkey: [a, b]\n---\nbody"
        meta, body = _parse_frontmatter(text)
        assert meta["key"] == ["a", "b"]

    def test_body_stripped(self):
        text = "---\nkey: val\n---\n\n\nbody starts here"
        meta, body = _parse_frontmatter(text)
        assert body == "body starts here"

    def test_empty_text_no_frontmatter(self):
        meta, body = _parse_frontmatter("")
        assert meta == {}
        assert body == ""

    def test_unclosed_frontmatter(self):
        text = "---\nkey: val\nbody without closing delimiter"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text


# ---------------------------------------------------------------------------
# is_eligible
# ---------------------------------------------------------------------------

class TestIsEligible:
    def test_silver_is_eligible(self):
        assert is_eligible(_make_entry(status="silver")) is True

    def test_bronze_not_eligible(self):
        assert is_eligible(_make_entry(status="bronze")) is False

    def test_gold_not_eligible(self):
        assert is_eligible(_make_entry(status="gold")) is False

    def test_failed_not_eligible(self):
        assert is_eligible(_make_entry(status="failed")) is False

    def test_skipped_large_not_eligible(self):
        assert is_eligible(_make_entry(status="skipped-large")) is False


# ---------------------------------------------------------------------------
# load_prompt
# ---------------------------------------------------------------------------

class TestLoadPrompt:
    def test_reads_file_content(self, prompt_path: Path):
        text = load_prompt(prompt_path)
        assert "Extract Playbook" in text

    def test_raises_if_missing(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_prompt(tmp_path / "nonexistent.md")


# ---------------------------------------------------------------------------
# _parse_entry_block
# ---------------------------------------------------------------------------

class TestParseEntryBlock:
    def test_valid_block_parses_correctly(self):
        entry = _parse_entry_block(VALID_ENTRY_BLOCK)
        assert entry["id"] == "mem-test-entry"
        assert entry["type"] == "operational"
        assert entry["topics"] == ["testing", "python"]
        assert entry["projects"] == ["all"]
        assert entry["agents"] == ["all"]
        assert entry["confidence"] == "medium"
        assert entry["source_sessions"] == ["sess-abc-123"]
        assert entry["created"] == "2026-04-17"
        assert entry["last_validated"] == "2026-04-17"
        assert entry["last_queried"] is None
        assert entry["query_count"] == 0
        assert entry["staleness_policy"] == "90d"
        assert entry["supersedes"] == []
        assert "Test Entry" in entry["body"]

    def test_missing_id_raises_value_error(self):
        block = """\
---
type: operational
topics: [testing]
---

# No ID entry
"""
        with pytest.raises(ValueError, match="id"):
            _parse_entry_block(block)

    def test_missing_type_raises_value_error(self):
        block = """\
---
id: mem-no-type
topics: [testing]
---

# No type entry
"""
        with pytest.raises(ValueError, match="type"):
            _parse_entry_block(block)

    def test_missing_frontmatter_delimiters_raises(self):
        block = "id: mem-no-delimiters\ntype: operational\n\nBody text"
        with pytest.raises(ValueError, match="delimiters"):
            _parse_entry_block(block)

    def test_empty_list_parses(self):
        block = """\
---
id: mem-empty-lists
type: pattern
topics: []
projects: []
agents: []
confidence: medium
source_sessions: []
created: 2026-04-17
last_validated: 2026-04-17
last_queried: null
query_count: 0
staleness_policy: never
supersedes: []
---

Body.
"""
        entry = _parse_entry_block(block)
        assert entry["topics"] == []
        assert entry["supersedes"] == []

    def test_integer_field_is_int(self):
        entry = _parse_entry_block(VALID_ENTRY_BLOCK)
        assert isinstance(entry["query_count"], int)
        assert entry["query_count"] == 0

    def test_null_field_is_none(self):
        entry = _parse_entry_block(VALID_ENTRY_BLOCK)
        assert entry["last_queried"] is None


# ---------------------------------------------------------------------------
# validate_frontmatter
# ---------------------------------------------------------------------------

class TestValidateFrontmatter:
    def test_all_fields_present_returns_true(self):
        entry = _make_valid_entry_dict()
        assert validate_frontmatter(entry) is True

    def test_missing_python_owned_field_is_ok(self):
        # confidence is Python-owned; validation only checks model-owned fields.
        entry = _make_valid_entry_dict()
        del entry["confidence"]
        assert validate_frontmatter(entry) is True

    def test_missing_id_returns_false(self):
        entry = _make_valid_entry_dict()
        del entry["id"]
        assert validate_frontmatter(entry) is False

    def test_missing_source_sessions_is_ok(self):
        # source_sessions is Python-owned; validation does not require it.
        entry = _make_valid_entry_dict()
        del entry["source_sessions"]
        assert validate_frontmatter(entry) is True

    def test_missing_staleness_policy_returns_false(self):
        entry = _make_valid_entry_dict()
        del entry["staleness_policy"]
        assert validate_frontmatter(entry) is False

    def test_missing_type_returns_false(self):
        entry = _make_valid_entry_dict()
        del entry["type"]
        assert validate_frontmatter(entry) is False

    def test_missing_body_returns_false(self):
        # Body is now part of the model-owned required set.
        entry = _make_valid_entry_dict()
        del entry["body"]
        assert validate_frontmatter(entry) is False


# ---------------------------------------------------------------------------
# call_claude_extract
# ---------------------------------------------------------------------------

_DEFAULT_META = {"project": "myproject", "agent": "developer"}
_DEFAULT_SILVER_BODY = "silver content"


class TestCallClaudeExtract:
    def test_dry_run_returns_empty_list(self, silver_dir: Path):
        silver_file = _write_silver(silver_dir, "sess-001")
        result = call_claude_extract(
            silver_file, "prompt text", "sess-001",
            metadata=_DEFAULT_META, silver_body=_DEFAULT_SILVER_BODY, dry_run=True,
        )
        assert result == []

    def test_no_new_knowledge_returns_empty_list(self, silver_dir: Path):
        silver_file = _write_silver(silver_dir, "sess-001")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NO_NEW_KNOWLEDGE"
        mock_result.stderr = ""

        with patch("scripts.extract.subprocess.run", return_value=mock_result):
            result = call_claude_extract(
                silver_file, "prompt", "sess-001",
                metadata=_DEFAULT_META, silver_body=_DEFAULT_SILVER_BODY, dry_run=False,
            )

        assert result == []

    def test_successful_parse_of_entry_block(self, silver_dir: Path):
        silver_file = _write_silver(silver_dir, "sess-001")

        output = (
            "Some preamble text.\n"
            "===ENTRY START===\n"
            + VALID_ENTRY_BLOCK
            + "===ENTRY END===\n"
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = output
        mock_result.stderr = ""

        with patch("scripts.extract.subprocess.run", return_value=mock_result):
            result = call_claude_extract(
                silver_file, "prompt", "sess-001",
                metadata=_DEFAULT_META, silver_body=_DEFAULT_SILVER_BODY, dry_run=False,
            )

        assert len(result) == 1
        assert result[0]["id"] == "mem-test-entry"
        assert result[0]["type"] == "operational"

    def test_claude_failure_raises_runtime_error(self, silver_dir: Path):
        silver_file = _write_silver(silver_dir, "sess-001")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "something went wrong"

        with patch("scripts.extract.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="claude -p failed"):
                call_claude_extract(
                    silver_file, "prompt", "sess-001",
                    metadata=_DEFAULT_META, silver_body=_DEFAULT_SILVER_BODY, dry_run=False,
                )

    def test_session_id_injected_into_prompt(self, silver_dir: Path):
        silver_file = _write_silver(silver_dir, "sess-abc")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NO_NEW_KNOWLEDGE"
        mock_result.stderr = ""

        with patch("scripts.extract.subprocess.run", return_value=mock_result) as mock_run:
            call_claude_extract(
                silver_file, "PROMPT", "sess-abc",
                metadata=_DEFAULT_META, silver_body=_DEFAULT_SILVER_BODY, dry_run=False,
            )

        # The combined input passed to claude should contain the session ID
        cmd_args = mock_run.call_args[0][0]
        combined_input = cmd_args[-1]  # last positional arg is the combined prompt
        assert "sess-abc" in combined_input

    def test_metadata_injected_into_prompt(self, silver_dir: Path):
        silver_file = _write_silver(silver_dir, "sess-meta")
        meta = {"project": "myproject", "agent": "developer"}

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NO_NEW_KNOWLEDGE"
        mock_result.stderr = ""

        with patch("scripts.extract.subprocess.run", return_value=mock_result) as mock_run:
            call_claude_extract(
                silver_file, "PROMPT", "sess-meta",
                metadata=meta, silver_body="body content", dry_run=False,
            )

        cmd_args = mock_run.call_args[0][0]
        combined_input = cmd_args[-1]
        assert "project: myproject" in combined_input
        assert "agent: developer" in combined_input

    def test_silver_body_not_frontmatter(self, silver_dir: Path):
        """The silver_body passed directly is used, not re-read from disk."""
        silver_file = _write_silver(silver_dir, "sess-body", "disk content -- should not appear")
        body = "injected body content"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NO_NEW_KNOWLEDGE"
        mock_result.stderr = ""

        with patch("scripts.extract.subprocess.run", return_value=mock_result) as mock_run:
            call_claude_extract(
                silver_file, "PROMPT", "sess-body",
                metadata=_DEFAULT_META, silver_body=body, dry_run=False,
            )

        cmd_args = mock_run.call_args[0][0]
        combined_input = cmd_args[-1]
        assert "injected body content" in combined_input
        assert "disk content" not in combined_input

    def test_malformed_block_is_skipped_with_warning(self, silver_dir: Path, caplog):
        silver_file = _write_silver(silver_dir, "sess-001")

        # Block missing the 'type' field will fail parsing
        bad_block = """\
---
id: mem-bad-entry
---

Body.
"""
        output = (
            "===ENTRY START===\n"
            + bad_block
            + "===ENTRY END===\n"
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = output
        mock_result.stderr = ""

        import logging
        with caplog.at_level(logging.WARNING):
            with patch("scripts.extract.subprocess.run", return_value=mock_result):
                result = call_claude_extract(
                    silver_file, "prompt", "sess-001",
                    metadata=_DEFAULT_META, silver_body=_DEFAULT_SILVER_BODY, dry_run=False,
                )

        assert result == []
        assert "Failed to parse" in caplog.text


# ---------------------------------------------------------------------------
# is_duplicate
# ---------------------------------------------------------------------------

class TestIsDuplicate:
    def test_no_collision_returns_false(self, tmp_path: Path):
        """Entry with no matching .md file in gold_dir is not a duplicate."""
        entry = _make_valid_entry_dict("mem-new-entry")
        assert is_duplicate(entry, tmp_path) is False

    def test_exact_id_collision_returns_true(self, tmp_path: Path):
        """Entry whose id matches an existing .md file is a duplicate."""
        entry = _make_valid_entry_dict("mem-existing-entry")
        (tmp_path / "mem-existing-entry.md").write_text("existing", encoding="utf-8")
        assert is_duplicate(entry, tmp_path) is True

    def test_topically_similar_different_id_not_duplicate(self, tmp_path: Path):
        """Regression guard: topically similar entry with different id is NOT a duplicate.

        This is the exact scenario that triggered the Apr 2026 silent gold-drop bug:
        mem-clock-based-subscription-expiry was rejected because mem-billing-access-packages
        scored 0.93 in the qmd reranker on a thin topic-only query. With exact-id collision
        only, these entries are correctly treated as distinct.
        """
        (tmp_path / "mem-billing-access-packages-source-of-truth.md").write_text(
            "billing content", encoding="utf-8"
        )
        entry = _make_valid_entry_dict("mem-clock-based-subscription-expiry")
        entry["topics"] = ["billing", "subscriptions", "expiry"]
        assert is_duplicate(entry, tmp_path) is False

    def test_empty_gold_dir_never_duplicate(self, tmp_path: Path):
        """No entries in an empty gold_dir can be duplicates."""
        entry = _make_valid_entry_dict("mem-any-entry")
        assert is_duplicate(entry, tmp_path) is False


class TestIsRecoverable:
    def test_gold_with_empty_gold_paths_is_recoverable(self):
        entry = {"status": "gold", "gold_paths": []}
        assert is_recoverable(entry) is True

    def test_gold_with_non_empty_gold_paths_not_recoverable(self):
        entry = {"status": "gold", "gold_paths": ["/some/path.md"]}
        assert is_recoverable(entry) is False

    def test_silver_status_not_recoverable(self):
        entry = {"status": "silver", "gold_paths": []}
        assert is_recoverable(entry) is False

    def test_missing_gold_paths_not_recoverable(self):
        """An entry without gold_paths at all is not a stuck gold entry."""
        entry = {"status": "gold"}
        assert is_recoverable(entry) is False


# ---------------------------------------------------------------------------
# write_gold_entry
# ---------------------------------------------------------------------------

class TestWriteGoldEntry:
    def test_dry_run_does_not_write_file(self, gold_dir: Path):
        entry = _make_valid_entry_dict()
        path = write_gold_entry(entry, gold_dir, dry_run=True)

        # No file should be written
        assert not path.exists()

    def test_real_write_creates_file_with_correct_content(self, gold_dir: Path):
        entry = _make_valid_entry_dict("mem-my-test")
        path = write_gold_entry(entry, gold_dir, dry_run=False)

        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "id: mem-my-test" in content
        assert "type: operational" in content
        assert "confidence: medium" in content
        assert "query_count: 0" in content
        assert "last_queried: null" in content
        assert "# Test Entry" in content

    def test_creates_gold_dir_if_missing(self, tmp_path: Path):
        deep_dir = tmp_path / "a" / "b" / "entries"
        entry = _make_valid_entry_dict("mem-deep-test")
        path = write_gold_entry(entry, deep_dir, dry_run=False)

        assert deep_dir.exists()
        assert path.exists()

    def test_list_fields_rendered_as_flow_style(self, gold_dir: Path):
        entry = _make_valid_entry_dict("mem-list-test")
        entry["topics"] = ["hippo", "pipeline", "gold"]
        path = write_gold_entry(entry, gold_dir, dry_run=False)

        content = path.read_text(encoding="utf-8")
        assert "topics: [hippo, pipeline, gold]" in content

    def test_body_included_after_frontmatter(self, gold_dir: Path):
        entry = _make_valid_entry_dict("mem-body-test")
        entry["body"] = "# My Entry\n\nSpecific detail: use port 9333."
        path = write_gold_entry(entry, gold_dir, dry_run=False)

        content = path.read_text(encoding="utf-8")
        parts = content.split("---\n\n")
        assert len(parts) >= 2
        assert "port 9333" in parts[-1]

    def test_returns_absolute_path(self, gold_dir: Path):
        entry = _make_valid_entry_dict("mem-abs-path")
        path = write_gold_entry(entry, gold_dir, dry_run=False)
        assert path.is_absolute()

    def test_dry_run_returns_absolute_path(self, gold_dir: Path):
        entry = _make_valid_entry_dict("mem-dry-abs")
        path = write_gold_entry(entry, gold_dir, dry_run=True)
        assert path.is_absolute()


# ---------------------------------------------------------------------------
# reindex_qmd
# ---------------------------------------------------------------------------

class TestReindexQmd:
    def test_dry_run_does_not_call_subprocess(self):
        with patch("scripts.extract.subprocess.run") as mock_run:
            reindex_qmd(dry_run=True)
        mock_run.assert_not_called()

    def test_dry_run_logs_message(self, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            with patch("scripts.extract.subprocess.run"):
                reindex_qmd(dry_run=True)
        assert "dry-run" in caplog.text.lower() or "Would run" in caplog.text

    def test_real_run_calls_qmd_update_and_embed(self):
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("scripts.extract.subprocess.run", return_value=mock_result) as mock_run:
            reindex_qmd(dry_run=False)

        assert mock_run.call_count == 2
        first_cmd = mock_run.call_args_list[0][0][0]
        second_cmd = mock_run.call_args_list[1][0][0]
        assert "qmd" in first_cmd
        assert "update" in first_cmd
        assert "qmd" in second_cmd
        assert "embed" in second_cmd

    def test_qmd_failure_logs_warning_not_raise(self, caplog):
        import logging
        mock_result = MagicMock()
        mock_result.returncode = 1

        with caplog.at_level(logging.WARNING):
            with patch("scripts.extract.subprocess.run", return_value=mock_result):
                # Should not raise
                reindex_qmd(dry_run=False)

        assert "failed" in caplog.text.lower() or "warning" in caplog.text.lower()

    def test_collection_name_used_in_commands(self):
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("scripts.extract.subprocess.run", return_value=mock_result) as mock_run:
            reindex_qmd(dry_run=False)

        for cmd_call in mock_run.call_args_list:
            cmd = cmd_call[0][0]
            assert QMD_COLLECTION in cmd


# ---------------------------------------------------------------------------
# extract_session
# ---------------------------------------------------------------------------

class TestExtractSession:
    def test_no_knowledge_updates_manifest_with_empty_gold_paths(
        self, silver_dir, gold_dir, tmp_manifest
    ):
        silver_file = _write_silver(silver_dir, "sess-001")
        entry = _make_entry(session_id="sess-001", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        with patch("scripts.extract.call_claude_extract", return_value=[]):
            written = extract_session(
                entry=entry,
                gold_dir=gold_dir,
                manifest_path=tmp_manifest,
                prompt="prompt",
                dry_run=False,
            )

        assert written == []
        manifested = read_manifest(str(tmp_manifest))
        updated = next(e for e in manifested if e["session_id"] == "sess-001")
        assert updated["status"] == "gold"
        assert updated["gold_paths"] == []
        assert updated["extracted_at"] is not None

    def test_valid_entry_written_and_manifest_updated(
        self, silver_dir, gold_dir, tmp_manifest
    ):
        silver_file = _write_silver(silver_dir, "sess-002")
        entry = _make_entry(session_id="sess-002", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        proposed = _make_valid_entry_dict("mem-new-entry")

        with patch("scripts.extract.call_claude_extract", return_value=[proposed]):
            with patch("scripts.extract.is_duplicate", return_value=False):
                written = extract_session(
                    entry=entry,
                    gold_dir=gold_dir,
                    manifest_path=tmp_manifest,
                    prompt="prompt",
                    dry_run=False,
                )

        assert len(written) == 1
        manifested = read_manifest(str(tmp_manifest))
        updated = next(e for e in manifested if e["session_id"] == "sess-002")
        assert updated["status"] == "gold"
        assert len(updated["gold_paths"]) == 1

    def test_duplicate_entry_skipped(
        self, silver_dir, gold_dir, tmp_manifest
    ):
        silver_file = _write_silver(silver_dir, "sess-003")
        entry = _make_entry(session_id="sess-003", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        proposed = _make_valid_entry_dict("mem-dup-entry")

        with patch("scripts.extract.call_claude_extract", return_value=[proposed]):
            with patch("scripts.extract.is_duplicate", return_value=True):
                written = extract_session(
                    entry=entry,
                    gold_dir=gold_dir,
                    manifest_path=tmp_manifest,
                    prompt="prompt",
                    dry_run=False,
                )

        assert written == []
        manifested = read_manifest(str(tmp_manifest))
        updated = next(e for e in manifested if e["session_id"] == "sess-003")
        assert updated["status"] == "gold"
        assert updated["gold_paths"] == []

    def test_invalid_frontmatter_skipped_with_warning(
        self, silver_dir, gold_dir, tmp_manifest, caplog
    ):
        silver_file = _write_silver(silver_dir, "sess-004")
        entry = _make_entry(session_id="sess-004", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        # Missing several required fields
        bad_entry = {"id": "mem-bad", "type": "operational", "body": "body"}

        import logging
        with caplog.at_level(logging.WARNING):
            with patch("scripts.extract.call_claude_extract", return_value=[bad_entry]):
                written = extract_session(
                    entry=entry,
                    gold_dir=gold_dir,
                    manifest_path=tmp_manifest,
                    prompt="prompt",
                    dry_run=False,
                )

        assert written == []
        assert "missing frontmatter" in caplog.text.lower() or "Skipping entry" in caplog.text

    def test_dry_run_does_not_update_manifest(
        self, silver_dir, gold_dir, tmp_manifest
    ):
        silver_file = _write_silver(silver_dir, "sess-005")
        entry = _make_entry(session_id="sess-005", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        proposed = _make_valid_entry_dict("mem-dry-entry")

        with patch("scripts.extract.call_claude_extract", return_value=[proposed]):
            with patch("scripts.extract.is_duplicate", return_value=False):
                extract_session(
                    entry=entry,
                    gold_dir=gold_dir,
                    manifest_path=tmp_manifest,
                    prompt="prompt",
                    dry_run=True,
                )

        manifested = read_manifest(str(tmp_manifest))
        unchanged = next(e for e in manifested if e["session_id"] == "sess-005")
        assert unchanged["status"] == "silver"  # not updated in dry_run

    def test_silver_frontmatter_metadata_propagated(
        self, silver_dir, gold_dir, tmp_manifest
    ):
        """Metadata from silver frontmatter is passed to call_claude_extract."""
        silver_content = (
            "---\n"
            "session_id: sess-006\n"
            "project_hash: -Users-mu-src-testproject\n"
            "project: testproject\n"
            "agent: developer\n"
            "parent_session: null\n"
            "ingested_at: 2026-04-17T10:00:00+00:00\n"
            "harness: claude-code\n"
            "---\n\n"
            "Silver body text here.\n"
        )
        silver_file = silver_dir / "sess-006.md"
        silver_file.write_text(silver_content, encoding="utf-8")

        entry = _make_entry(session_id="sess-006", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        captured_kwargs: dict = {}

        def fake_call_extract(silver_path, prompt, session_id, metadata, silver_body, dry_run):
            captured_kwargs["metadata"] = metadata
            captured_kwargs["silver_body"] = silver_body
            return []

        with patch("scripts.extract.call_claude_extract", side_effect=fake_call_extract):
            extract_session(
                entry=entry,
                gold_dir=gold_dir,
                manifest_path=tmp_manifest,
                prompt="prompt",
                dry_run=False,
            )

        assert captured_kwargs["metadata"].get("project") == "testproject"
        assert captured_kwargs["metadata"].get("agent") == "developer"
        assert "Silver body text here." in captured_kwargs["silver_body"]

    def test_project_from_silver_written_to_gold_entry(
        self, silver_dir, gold_dir, tmp_manifest
    ):
        """Project name from silver frontmatter must appear in the written gold entry."""
        silver_content = (
            "---\n"
            "session_id: sess-p1\n"
            "project: kenznote-payments\n"
            "agent: developer\n"
            "---\n\n"
            "Silver body.\n"
        )
        silver_file = silver_dir / "sess-p1.md"
        silver_file.write_text(silver_content, encoding="utf-8")

        entry = _make_entry(session_id="sess-p1", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        proposed = _make_valid_entry_dict("mem-proj-test")
        proposed["projects"] = ["all"]  # model would emit [all] without the fix

        with patch("scripts.extract.call_claude_extract", return_value=[proposed]):
            with patch("scripts.extract.is_duplicate", return_value=False):
                written = extract_session(
                    entry=entry,
                    gold_dir=gold_dir,
                    manifest_path=tmp_manifest,
                    prompt="prompt",
                    dry_run=False,
                )

        assert len(written) == 1
        content = Path(written[0]).read_text(encoding="utf-8")
        assert "projects: [kenznote-payments]" in content
        assert "projects: [all]" not in content

    def test_backward_compat_no_frontmatter(
        self, silver_dir, gold_dir, tmp_manifest
    ):
        """Old silver files without frontmatter fall back to manifest entry metadata."""
        silver_file = _write_silver(silver_dir, "sess-007", "plain silver body without frontmatter")
        entry = _make_entry(
            session_id="sess-007",
            silver_path=str(silver_file),
            project_hash="-Users-mu-src-fallbackproject",
            agent="tester",
        )
        _write_manifest(tmp_manifest, [entry])

        captured_kwargs: dict = {}

        def fake_call_extract(silver_path, prompt, session_id, metadata, silver_body, dry_run):
            captured_kwargs["metadata"] = metadata
            captured_kwargs["silver_body"] = silver_body
            return []

        with patch("scripts.extract.call_claude_extract", side_effect=fake_call_extract):
            extract_session(
                entry=entry,
                gold_dir=gold_dir,
                manifest_path=tmp_manifest,
                prompt="prompt",
                dry_run=False,
            )

        # Fallback: project comes from project_hash in manifest entry
        assert captured_kwargs["metadata"].get("project") == "-Users-mu-src-fallbackproject"
        assert captured_kwargs["metadata"].get("agent") == "tester"
        # Body is the full file content (no frontmatter stripped)
        assert "plain silver body without frontmatter" in captured_kwargs["silver_body"]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def test_dry_run_no_subprocess(self, tmp_path, silver_dir, gold_dir, prompt_path, tmp_manifest):
        """main --dry-run must not invoke subprocess.run."""
        silver_file = _write_silver(silver_dir, "s1")
        entry = _make_entry(session_id="s1", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        with patch("scripts.extract.subprocess.run") as mock_run:
            rc = main([
                "--dry-run",
                "--manifest", str(tmp_manifest),
                "--gold-dir", str(gold_dir),
                "--prompt", str(prompt_path),
            ])

        assert rc == 0
        mock_run.assert_not_called()

    def test_gold_entries_already_extracted_skipped(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest
    ):
        """Sessions already at status gold must be skipped (idempotent)."""
        silver_file = _write_silver(silver_dir, "s-gold")
        entry = _make_entry(session_id="s-gold", silver_path=str(silver_file), status="gold")
        _write_manifest(tmp_manifest, [entry])

        with patch("scripts.extract.subprocess.run") as mock_run:
            rc = main([
                "--manifest", str(tmp_manifest),
                "--gold-dir", str(gold_dir),
                "--prompt", str(prompt_path),
            ])

        assert rc == 0
        mock_run.assert_not_called()

    def test_limit_flag_processes_at_most_n_sessions(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest
    ):
        """--limit N processes at most N sessions."""
        s1 = _write_silver(silver_dir, "s1")
        s2 = _write_silver(silver_dir, "s2")
        s3 = _write_silver(silver_dir, "s3")
        entries = [
            _make_entry(session_id="s1", silver_path=str(s1)),
            _make_entry(session_id="s2", silver_path=str(s2)),
            _make_entry(session_id="s3", silver_path=str(s3)),
        ]
        _write_manifest(tmp_manifest, entries)

        call_tracker = []

        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            call_tracker.append(entry["session_id"])
            return []

        with patch("scripts.extract.extract_session", side_effect=fake_extract):
            rc = main([
                "--limit", "2",
                "--manifest", str(tmp_manifest),
                "--gold-dir", str(gold_dir),
                "--prompt", str(prompt_path),
            ])

        assert rc == 0
        assert len(call_tracker) == 2
        assert call_tracker == ["s1", "s2"]

    def test_session_flag_processes_only_that_session(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest
    ):
        """--session processes only the named session, ignoring others."""
        s1 = _write_silver(silver_dir, "s1")
        s2 = _write_silver(silver_dir, "s2")
        entries = [
            _make_entry(session_id="s1", silver_path=str(s1)),
            _make_entry(session_id="s2", silver_path=str(s2)),
        ]
        _write_manifest(tmp_manifest, entries)

        call_tracker = []

        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            call_tracker.append(entry["session_id"])
            return []

        with patch("scripts.extract.extract_session", side_effect=fake_extract):
            rc = main([
                "--session", "s1",
                "--manifest", str(tmp_manifest),
                "--gold-dir", str(gold_dir),
                "--prompt", str(prompt_path),
            ])

        assert rc == 0
        assert call_tracker == ["s1"]

    def test_session_flag_missing_returns_error(
        self, gold_dir, prompt_path, tmp_manifest
    ):
        """--session with unknown session_id returns non-zero exit code."""
        _write_manifest(tmp_manifest, [])
        rc = main([
            "--session", "nonexistent",
            "--manifest", str(tmp_manifest),
            "--gold-dir", str(gold_dir),
            "--prompt", str(prompt_path),
        ])
        assert rc != 0

    def test_failure_sets_status_failed_and_continues(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest
    ):
        """Per-session exception sets status=failed and continues to next session."""
        s1 = _write_silver(silver_dir, "s1")
        s2 = _write_silver(silver_dir, "s2")
        entries = [
            _make_entry(session_id="s1", silver_path=str(s1)),
            _make_entry(session_id="s2", silver_path=str(s2)),
        ]
        _write_manifest(tmp_manifest, entries)

        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            if entry["session_id"] == "s1":
                raise RuntimeError("claude exploded")
            return []

        with patch("scripts.extract.extract_session", side_effect=fake_extract):
            rc = main([
                "--manifest", str(tmp_manifest),
                "--gold-dir", str(gold_dir),
                "--prompt", str(prompt_path),
            ])

        # Returns 1 because there was an error
        assert rc == 1

        manifested = read_manifest(str(tmp_manifest))
        s1_entry = next(e for e in manifested if e["session_id"] == "s1")
        assert s1_entry["status"] == "failed"
        assert s1_entry["error"] is not None

    def test_reindex_called_when_entries_written(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest
    ):
        """reindex_qmd should be called when gold entries are written."""
        silver_file = _write_silver(silver_dir, "s1")
        entry = _make_entry(session_id="s1", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            return ["/some/path/mem-entry.md"]

        with patch("scripts.extract.extract_session", side_effect=fake_extract):
            with patch("scripts.extract.reindex_qmd") as mock_reindex:
                rc = main([
                    "--manifest", str(tmp_manifest),
                    "--gold-dir", str(gold_dir),
                    "--prompt", str(prompt_path),
                ])

        assert rc == 0
        mock_reindex.assert_called_once_with(dry_run=False)

    def test_reindex_not_called_when_no_entries_written(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest
    ):
        """reindex_qmd should not be called when no gold entries are written."""
        silver_file = _write_silver(silver_dir, "s1")
        entry = _make_entry(session_id="s1", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            return []

        with patch("scripts.extract.extract_session", side_effect=fake_extract):
            with patch("scripts.extract.reindex_qmd") as mock_reindex:
                rc = main([
                    "--manifest", str(tmp_manifest),
                    "--gold-dir", str(gold_dir),
                    "--prompt", str(prompt_path),
                ])

        assert rc == 0
        mock_reindex.assert_not_called()


# ---------------------------------------------------------------------------
# S3.1: dedupe by silver_path for chunked sessions
# ---------------------------------------------------------------------------

class TestMainDedupesBySilverPath:
    def test_chunked_session_extracted_once_per_silver(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest
    ):
        """Three silver entries pointing to one silver file should extract once."""
        # All three parts share the same silver_path (chunked session).
        shared_silver = _write_silver(silver_dir, "abc")  # one silver file
        entries = [
            _make_entry(
                session_id=f"abc_part_{i:02d}",
                silver_path=str(shared_silver),
                part_index=i, total_parts=3,
            )
            for i in range(1, 4)
        ]
        _write_manifest(tmp_manifest, entries)

        call_tracker = []

        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            call_tracker.append(entry["session_id"])
            return []

        with patch("scripts.extract.extract_session", side_effect=fake_extract):
            rc = main([
                "--manifest", str(tmp_manifest),
                "--gold-dir", str(gold_dir),
                "--prompt", str(prompt_path),
            ])

        assert rc == 0
        # extract_session called exactly once — for the canonical (highest part_index) entry.
        assert len(call_tracker) == 1
        assert call_tracker[0] == "abc_part_03"

    def test_sibling_parts_advanced_to_gold(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest
    ):
        """After extraction, all sibling parts are bumped to status=gold."""
        from scripts.manifest import read_manifest as _rm
        shared_silver = _write_silver(silver_dir, "xyz")
        entries = [
            _make_entry(
                session_id=f"xyz_part_{i:02d}",
                silver_path=str(shared_silver),
                part_index=i, total_parts=3,
            )
            for i in range(1, 4)
        ]
        _write_manifest(tmp_manifest, entries)

        # Make extract_session simulate a normal run that updates the canonical
        # entry to gold (mirrors real extract behaviour).
        from scripts.manifest import update_manifest as _um
        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            _um(str(manifest_path), entry["session_id"], {"status": "gold", "gold_paths": ["/g/foo.md"]})
            return ["/g/foo.md"]

        with patch("scripts.extract.extract_session", side_effect=fake_extract):
            rc = main([
                "--manifest", str(tmp_manifest),
                "--gold-dir", str(gold_dir),
                "--prompt", str(prompt_path),
            ])
        assert rc == 0

        # All three parts should now be gold.
        m = _rm(str(tmp_manifest))
        statuses = {e["session_id"]: e["status"] for e in m}
        assert all(s == "gold" for s in statuses.values()), statuses

    def test_unsplit_and_chunked_mix(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest
    ):
        """Unsplit sessions and chunked sessions both extract correctly."""
        unsplit_silver = _write_silver(silver_dir, "u1")
        chunked_silver = _write_silver(silver_dir, "c1")
        entries = [
            _make_entry(session_id="u1", silver_path=str(unsplit_silver)),
            _make_entry(
                session_id="c1_part_01", silver_path=str(chunked_silver),
                part_index=1, total_parts=2,
            ),
            _make_entry(
                session_id="c1_part_02", silver_path=str(chunked_silver),
                part_index=2, total_parts=2,
            ),
        ]
        _write_manifest(tmp_manifest, entries)

        call_tracker = []
        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            call_tracker.append(entry["session_id"])
            return []
        with patch("scripts.extract.extract_session", side_effect=fake_extract):
            rc = main([
                "--manifest", str(tmp_manifest),
                "--gold-dir", str(gold_dir),
                "--prompt", str(prompt_path),
            ])
        assert rc == 0
        # 2 unique silvers → 2 extractions: unsplit + chunked-canonical
        assert sorted(call_tracker) == sorted(["u1", "c1_part_02"])


# ---------------------------------------------------------------------------
# S3.1: Python-owned frontmatter fields
# ---------------------------------------------------------------------------

from datetime import datetime, timezone
from scripts.extract import _apply_python_owned_fields, MODEL_OWNED_REQUIRED


class TestApplyPythonOwnedFields:
    def test_sets_source_sessions_from_session_id(self):
        entry = {"id": "mem-x", "type": "operational", "topics": ["a"],
                 "projects": ["all"], "agents": ["all"], "staleness_policy": "30d", "body": "..."}
        out = _apply_python_owned_fields(entry, "abc-123")
        assert out["source_sessions"] == ["abc-123"]

    def test_sets_today_for_dates(self):
        entry = {"body": "..."}
        out = _apply_python_owned_fields(entry, "x")
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        assert out["created"] == today
        assert out["last_validated"] == today

    def test_sets_fixed_defaults(self):
        out = _apply_python_owned_fields({}, "x")
        assert out["last_queried"] is None
        assert out["query_count"] == 0
        assert out["supersedes"] == []
        assert out["confidence"] == "medium"

    def test_overrides_model_attempts_to_set_python_owned(self):
        # If the model wrongly emits any Python-owned field, Python wins.
        entry = {
            "source_sessions": ["wrong-id"],
            "created": "1999-01-01",
            "last_validated": "1999-01-01",
            "last_queried": "yesterday",
            "query_count": 999,
            "confidence": "high",
        }
        out = _apply_python_owned_fields(entry, "real-session")
        assert out["source_sessions"] == ["real-session"]
        assert out["created"] != "1999-01-01"
        assert out["last_queried"] is None
        assert out["query_count"] == 0
        assert out["confidence"] == "medium"

    def test_supersedes_preserved_if_set(self):
        # Model can opt to set supersedes (rare); Python keeps non-empty value.
        entry = {"supersedes": ["mem-old-thing"]}
        out = _apply_python_owned_fields(entry, "x")
        assert out["supersedes"] == ["mem-old-thing"]

    def test_project_overrides_model_projects(self):
        # Whatever the model emits for `projects` (e.g. [all]), the real
        # project name from silver metadata must win.
        entry = {"projects": ["all"]}
        out = _apply_python_owned_fields(entry, "x", project="kenznote-payments")
        assert out["projects"] == ["kenznote-payments"]

    def test_unknown_project_leaves_model_value(self):
        entry = {"projects": ["all"]}
        out = _apply_python_owned_fields(entry, "x", project="unknown")
        assert out["projects"] == ["all"]

    def test_none_project_leaves_model_value(self):
        entry = {"projects": ["all"]}
        out = _apply_python_owned_fields(entry, "x", project=None)
        assert out["projects"] == ["all"]


class TestModelOwnedConstants:
    def test_model_owned_does_not_include_python_owned(self):
        python_owned = {"source_sessions", "created", "last_validated",
                        "last_queried", "query_count", "supersedes", "confidence"}
        assert MODEL_OWNED_REQUIRED.isdisjoint(python_owned)

    def test_model_owned_includes_required_semantic_fields(self):
        assert {"id", "type", "topics", "projects", "agents",
                "staleness_policy", "body"}.issubset(MODEL_OWNED_REQUIRED)


# ---------------------------------------------------------------------------
# MVP-2: Quota recovery (Issue 1) -- Task 7.1
# ---------------------------------------------------------------------------

from scripts.errors import QuotaExhaustedError


class TestExtractQuotaRecovery:
    """QuotaExhaustedError mid-run leaves no failed rows and exits 0."""

    def test_quota_wall_leaves_no_failed_rows(self, silver_dir, gold_dir, prompt_path, tmp_manifest):
        """When QuotaExhaustedError is raised during extraction, session status
        stays at 'silver' (no failed row written) and main() returns 0."""
        s1 = _write_silver(silver_dir, "s1")
        s2 = _write_silver(silver_dir, "s2")
        entries = [
            _make_entry(session_id="s1", silver_path=str(s1)),
            _make_entry(session_id="s2", silver_path=str(s2)),
        ]
        _write_manifest(tmp_manifest, entries)

        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            if entry["session_id"] == "s1":
                raise QuotaExhaustedError("quota hit")
            return []

        with patch("scripts.extract.extract_session", side_effect=fake_extract):
            rc = main([
                "--manifest", str(tmp_manifest),
                "--gold-dir", str(gold_dir),
                "--prompt", str(prompt_path),
            ])

        # Exit code 0: launchd does not flag job as failed.
        assert rc == 0

        # s1 must NOT be marked failed -- it stays silver.
        manifested = read_manifest(str(tmp_manifest))
        s1_entry = next(e for e in manifested if e["session_id"] == "s1")
        assert s1_entry["status"] == "silver"
        assert s1_entry.get("error") is None

    def test_quota_wall_stops_processing_remaining_sessions(self, silver_dir, gold_dir, prompt_path, tmp_manifest):
        """After a quota wall, the loop breaks and subsequent sessions are not processed."""
        s1 = _write_silver(silver_dir, "s1")
        s2 = _write_silver(silver_dir, "s2")
        entries = [
            _make_entry(session_id="s1", silver_path=str(s1)),
            _make_entry(session_id="s2", silver_path=str(s2)),
        ]
        _write_manifest(tmp_manifest, entries)

        processed = []

        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            processed.append(entry["session_id"])
            if entry["session_id"] == "s1":
                raise QuotaExhaustedError("quota hit")
            return []

        with patch("scripts.extract.extract_session", side_effect=fake_extract):
            main([
                "--manifest", str(tmp_manifest),
                "--gold-dir", str(gold_dir),
                "--prompt", str(prompt_path),
            ])

        # Only s1 was attempted; s2 was not processed after the quota stop.
        assert processed == ["s1"]


# ---------------------------------------------------------------------------
# AC2: --recover-empty-gold stuck session recovery
# ---------------------------------------------------------------------------

class TestRecoverEmptyGold:
    def test_recover_flag_includes_stuck_gold_sessions(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest
    ):
        """--recover-empty-gold causes gold-status sessions with empty gold_paths
        to be re-processed alongside silver sessions."""
        import logging
        from datetime import datetime, timezone

        silver_file = _write_silver(silver_dir, "s-silver")
        stuck_silver = _write_silver(silver_dir, "s-stuck")

        now_iso = datetime.now(tz=timezone.utc).isoformat()
        entries = [
            _make_entry(session_id="s-silver", silver_path=str(silver_file)),
            _make_entry(
                session_id="s-stuck",
                silver_path=str(stuck_silver),
                status="gold",
                gold_paths=[],
                extracted_at=now_iso,
            ),
        ]
        _write_manifest(tmp_manifest, entries)

        call_tracker = []

        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            call_tracker.append(entry["session_id"])
            return []

        with patch("scripts.extract.extract_session", side_effect=fake_extract):
            rc = main([
                "--recover-empty-gold",
                "--manifest", str(tmp_manifest),
                "--gold-dir", str(gold_dir),
                "--prompt", str(prompt_path),
            ])

        assert rc == 0
        assert "s-silver" in call_tracker
        assert "s-stuck" in call_tracker

    def test_without_recover_flag_stuck_sessions_skipped(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest
    ):
        """Without --recover-empty-gold, gold-status sessions are not re-processed."""
        from datetime import datetime, timezone

        stuck_silver = _write_silver(silver_dir, "s-stuck")
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        entries = [
            _make_entry(
                session_id="s-stuck",
                silver_path=str(stuck_silver),
                status="gold",
                gold_paths=[],
                extracted_at=now_iso,
            ),
        ]
        _write_manifest(tmp_manifest, entries)

        call_tracker = []

        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            call_tracker.append(entry["session_id"])
            return []

        with patch("scripts.extract.extract_session", side_effect=fake_extract):
            rc = main([
                "--manifest", str(tmp_manifest),
                "--gold-dir", str(gold_dir),
                "--prompt", str(prompt_path),
            ])

        assert rc == 0
        assert call_tracker == []

    def test_extract_session_writes_entry_when_no_id_collision(
        self, silver_dir, gold_dir, tmp_manifest
    ):
        """After the fix, extract_session writes the entry when no .md collision exists.

        This is the core regression test: previously is_duplicate() would false-positive
        on a topic-similar entry. Now it checks only for exact id file collision.
        """
        silver_file = _write_silver(silver_dir, "sess-recover")
        entry = _make_entry(session_id="sess-recover", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        proposed = _make_valid_entry_dict("mem-clock-based-subscription-expiry")
        proposed["topics"] = ["billing", "subscriptions", "expiry"]

        # Simulate corpus has a topically related entry but different id.
        gold_dir.mkdir(parents=True, exist_ok=True)
        (gold_dir / "mem-billing-access-packages-source-of-truth.md").write_text(
            "existing billing entry", encoding="utf-8"
        )

        with patch("scripts.extract.call_claude_extract", return_value=[proposed]):
            written = extract_session(
                entry=entry,
                gold_dir=gold_dir,
                manifest_path=tmp_manifest,
                prompt="prompt",
                dry_run=False,
            )

        # Entry must be written: different id, no collision.
        assert len(written) == 1
        assert (gold_dir / "mem-clock-based-subscription-expiry.md").exists()


# ---------------------------------------------------------------------------
# AC4: monitoring warning for empty gold_paths
# ---------------------------------------------------------------------------

class TestMonitoringWarning:
    def test_main_warns_on_empty_gold_paths_in_last_24h(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest, caplog
    ):
        """After a run that produces empty gold_paths, main() emits a WARNING.

        Regression guard: the Apr 2026 regression went undetected for 4 days because
        the pipeline reported success even when gold_paths was empty.
        """
        import logging
        from datetime import datetime, timezone

        # Session that extract_session marks as gold but with empty paths.
        silver_file = _write_silver(silver_dir, "s-empty")
        entry = _make_entry(session_id="s-empty", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            # Simulate the pipeline writing gold status with empty gold_paths.
            from scripts.manifest import update_manifest
            from datetime import datetime, timezone
            now_iso = datetime.now(tz=timezone.utc).isoformat()
            update_manifest(
                str(manifest_path),
                entry["session_id"],
                {"status": "gold", "extracted_at": now_iso, "gold_paths": [], "error": None},
            )
            return []

        with caplog.at_level(logging.WARNING, logger="scripts.extract"):
            with patch("scripts.extract.extract_session", side_effect=fake_extract):
                rc = main([
                    "--manifest", str(tmp_manifest),
                    "--gold-dir", str(gold_dir),
                    "--prompt", str(prompt_path),
                ])

        assert rc == 0
        warning_text = caplog.text
        assert "empty gold_paths" in warning_text.lower() or "gold_paths" in warning_text

    def test_main_no_warning_when_gold_paths_non_empty(
        self, silver_dir, gold_dir, prompt_path, tmp_manifest, caplog
    ):
        """When all gold sessions have non-empty gold_paths, no warning is emitted."""
        import logging
        from datetime import datetime, timezone

        silver_file = _write_silver(silver_dir, "s-ok")
        entry = _make_entry(session_id="s-ok", silver_path=str(silver_file))
        _write_manifest(tmp_manifest, [entry])

        def fake_extract(entry, gold_dir, manifest_path, prompt, dry_run):
            # Simulate successful extraction with written paths.
            from scripts.manifest import update_manifest
            from datetime import datetime, timezone
            now_iso = datetime.now(tz=timezone.utc).isoformat()
            update_manifest(
                str(manifest_path),
                entry["session_id"],
                {
                    "status": "gold",
                    "extracted_at": now_iso,
                    "gold_paths": ["/some/mem-ok.md"],
                    "error": None,
                },
            )
            return ["/some/mem-ok.md"]

        with caplog.at_level(logging.WARNING, logger="scripts.extract"):
            with patch("scripts.extract.extract_session", side_effect=fake_extract):
                with patch("scripts.extract.reindex_qmd"):
                    rc = main([
                        "--manifest", str(tmp_manifest),
                        "--gold-dir", str(gold_dir),
                        "--prompt", str(prompt_path),
                    ])

        assert rc == 0
        # No WARNING about empty gold_paths
        for record in caplog.records:
            if record.levelno == logging.WARNING:
                assert "gold_paths" not in record.message.lower()
