"""Unit tests for scripts/compact.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from scripts.compact import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_RETRIES,
    COMPACTION_MODEL,
    _project_slug,
    _silver_frontmatter,
    call_claude_compact,
    compact_session,
    is_eligible,
    load_prompt,
    log_compaction_ratio,
    main,
)
from scripts.manifest import read_manifest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_manifest(tmp_path: Path) -> Path:
    """Return path to a temporary manifest.jsonl file (does not exist yet)."""
    return tmp_path / "manifest.jsonl"


@pytest.fixture()
def silver_dir(tmp_path: Path) -> Path:
    """Return a temporary silver directory."""
    d = tmp_path / "silver"
    d.mkdir()
    return d


@pytest.fixture()
def bronze_dir(tmp_path: Path) -> Path:
    """Return a temporary bronze directory."""
    d = tmp_path / "bronze"
    d.mkdir()
    return d


@pytest.fixture()
def playbook_path(tmp_path: Path) -> Path:
    """Write a minimal compact.md prompt and return its path."""
    p = tmp_path / "compact.md"
    p.write_text("# Compact Playbook\n\nPreserve near-miss failures.\n", encoding="utf-8")
    return p


def _write_bronze(bronze_dir: Path, session_id: str, content: str = "bronze content") -> Path:
    """Write a fake bronze file and return its path."""
    p = bronze_dir / f"{session_id}.jsonl"
    p.write_text(content, encoding="utf-8")
    return p


def _write_manifest(manifest_path: Path, entries: list[dict]) -> None:
    """Write a list of manifest entries as JSONL."""
    with open(manifest_path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def _make_entry(session_id: str = "sess-001", status: str = "bronze", **kwargs) -> dict:
    """Return a minimal manifest entry dict."""
    return {
        "session_id": session_id,
        "source_path": f"/src/{session_id}.jsonl",
        "bronze_path": "",  # overridden in tests that need it
        "silver_path": None,
        "gold_paths": [],
        "status": status,
        "ingested_at": "2026-04-17T10:00:00+00:00",
        "compacted_at": None,
        "extracted_at": None,
        "bronze_size_bytes": 50_000,
        "silver_size_bytes": None,
        "error": None,
        "harness": "claude-code",
        "project_hash": "-Users-mu-src-myproject",
        "agent": None,
        "parent_session": None,
        "short": False,
        "memory_query": False,
        **kwargs,
    }


# ---------------------------------------------------------------------------
# _project_slug
# ---------------------------------------------------------------------------

class TestProjectSlug:
    def test_src_path(self):
        assert _project_slug("-Users-mu-src-transgate-frontend") == "transgate-frontend"

    def test_business_path(self):
        assert _project_slug("-Users-mu-Business-Kenznote-kenz-note-main") == "Kenznote-kenz-note-main"

    def test_hippo(self):
        assert _project_slug("-Users-mu-src-hippo") == "hippo"

    def test_unknown_pattern(self):
        assert _project_slug("some-other-hash") == "some-other-hash"

    def test_empty(self):
        assert _project_slug("") == ""


# ---------------------------------------------------------------------------
# _silver_frontmatter
# ---------------------------------------------------------------------------

class TestSilverFrontmatter:
    def _sample_entry(self, **kwargs) -> dict:
        base = {
            "session_id": "sess-fm-001",
            "project_hash": "-Users-mu-src-myapp",
            "agent": "developer",
            "parent_session": None,
            "ingested_at": "2026-04-17T10:00:00+00:00",
            "harness": "claude-code",
        }
        base.update(kwargs)
        return base

    def test_contains_required_fields(self):
        entry = self._sample_entry()
        result = _silver_frontmatter(entry)
        assert result.startswith("---")
        # Last non-empty line before body blank line is "---"
        assert "---" in result
        assert "session_id:" in result
        assert "project_hash:" in result
        assert "project:" in result
        assert "agent:" in result
        assert "parent_session:" in result
        assert "ingested_at:" in result
        assert "harness:" in result

    def test_null_agent(self):
        entry = self._sample_entry(agent=None)
        result = _silver_frontmatter(entry)
        assert "agent: null" in result

    def test_project_slug_applied(self):
        entry = self._sample_entry(project_hash="-Users-mu-src-myapp")
        result = _silver_frontmatter(entry)
        assert "project: myapp" in result

    def test_agent_task_present(self):
        """agent_task field is serialized in silver frontmatter when provided."""
        entry = self._sample_entry(agent_task="Review webhook ingestion code")
        result = _silver_frontmatter(entry)
        assert "agent_task: Review webhook ingestion code" in result

    def test_agent_task_null_when_absent(self):
        """agent_task serializes as null when missing from manifest entry."""
        entry = self._sample_entry()
        # Ensure agent_task key is not present (top-level session).
        entry.pop("agent_task", None)
        result = _silver_frontmatter(entry)
        assert "agent_task: null" in result

    def test_agent_task_adjacent_to_agent_and_parent(self):
        """agent_task appears between agent and parent_session in the frontmatter."""
        entry = self._sample_entry(agent_task="Implement payment flow")
        result = _silver_frontmatter(entry)
        lines = result.splitlines()
        agent_idx = next(i for i, l in enumerate(lines) if l.startswith("agent:"))
        agent_task_idx = next(i for i, l in enumerate(lines) if l.startswith("agent_task:"))
        parent_idx = next(i for i, l in enumerate(lines) if l.startswith("parent_session:"))
        assert agent_idx < agent_task_idx < parent_idx, (
            f"Expected agent({agent_idx}) < agent_task({agent_task_idx}) < parent_session({parent_idx})"
        )


# ---------------------------------------------------------------------------
# is_eligible
# ---------------------------------------------------------------------------

class TestIsEligible:
    def test_bronze_eligible(self):
        assert is_eligible(_make_entry(status="bronze")) is True

    def test_silver_not_eligible(self):
        assert is_eligible(_make_entry(status="silver")) is False

    def test_gold_not_eligible(self):
        assert is_eligible(_make_entry(status="gold")) is False

    def test_failed_not_eligible(self):
        assert is_eligible(_make_entry(status="failed")) is False

    def test_skipped_large_not_eligible(self):
        assert is_eligible(_make_entry(status="skipped-large")) is False

    def test_short_session_skipped(self):
        assert is_eligible(_make_entry(status="bronze", short=True)) is False

    def test_memory_query_skipped(self):
        assert is_eligible(_make_entry(status="bronze", memory_query=True)) is False

    def test_below_min_compact_bytes_not_eligible(self):
        assert is_eligible(_make_entry(status="bronze", bronze_size_bytes=10_000)) is False

    def test_bronze_short_false_memory_false_eligible(self):
        assert is_eligible(_make_entry(status="bronze", short=False, memory_query=False)) is True


# ---------------------------------------------------------------------------
# load_prompt
# ---------------------------------------------------------------------------

class TestLoadPrompt:
    def test_reads_file_content(self, playbook_path: Path):
        text = load_prompt(playbook_path)
        assert "Compact Playbook" in text

    def test_raises_if_missing(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_prompt(tmp_path / "nonexistent.md")


# ---------------------------------------------------------------------------
# log_compaction_ratio
# ---------------------------------------------------------------------------

class TestLogCompactionRatio:
    def test_zero_bronze_does_not_raise(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            log_compaction_ratio(0, 100)
        assert "0" in caplog.text or "cannot" in caplog.text.lower()

    def test_low_ratio_warns(self, caplog):
        import logging
        # ratio = 5 / 1000 = 0.5%, below 1% threshold (echo-failure floor)
        with caplog.at_level(logging.WARNING):
            log_compaction_ratio(1000, 5)
        assert "below" in caplog.text.lower()

    def test_high_ratio_warns(self, caplog):
        import logging
        # ratio = 500 / 1000 = 50%, above 30% threshold (under-compressed)
        with caplog.at_level(logging.WARNING):
            log_compaction_ratio(1000, 500)
        assert "above" in caplog.text.lower()

    def test_normal_ratio_no_warning(self, caplog):
        import logging
        # ratio = 100 / 1000 = 10%, within recalibrated [1%, 30%] band
        with caplog.at_level(logging.WARNING):
            log_compaction_ratio(1000, 100)
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert not warnings


# ---------------------------------------------------------------------------
# call_claude_compact
# ---------------------------------------------------------------------------

class TestCallClaudeCompact:
    def test_success_returns_stdout(self):
        """A successful claude call returns its stdout."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "# Silver Summary\n\nContent here.\n"
        mock_result.stderr = ""

        with patch("scripts.compact.subprocess.run", return_value=mock_result) as mock_run:
            output = call_claude_compact("prompt text", "bronze content")

        assert output == "# Silver Summary\n\nContent here.\n"
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert "claude" in cmd
        assert "--model" in cmd
        assert COMPACTION_MODEL in cmd

    def test_non_rate_limit_error_raises_immediately(self):
        """A non-rate-limit failure raises RuntimeError without retrying."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "fatal error: something went wrong"

        with patch("scripts.compact.subprocess.run", return_value=mock_result) as mock_run:
            with pytest.raises(RuntimeError, match="claude -p failed"):
                call_claude_compact("prompt", "content")

        assert mock_run.call_count == 1  # no retries

    def test_rate_limit_retries_then_raises(self):
        """Rate-limit errors trigger exponential backoff and raise after max retries."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "429 rate limit exceeded"

        total_attempts = BACKOFF_MAX_RETRIES + 1  # initial + retries

        with patch("scripts.compact.subprocess.run", return_value=mock_result) as mock_run:
            with patch("scripts.compact.time.sleep") as mock_sleep:
                with pytest.raises(RuntimeError, match="Exhausted"):
                    call_claude_compact("prompt", "content")

        assert mock_run.call_count == total_attempts
        # Verify exponential delays: 2s, 4s, 8s
        expected_delays = [BACKOFF_BASE_SECONDS * (2 ** i) for i in range(BACKOFF_MAX_RETRIES)]
        actual_delays = [c[0][0] for c in mock_sleep.call_args_list]
        assert actual_delays == expected_delays

    def test_rate_limit_429_in_returncode_retries(self):
        """Return code 429 is treated as a rate limit."""
        mock_result = MagicMock()
        mock_result.returncode = 429
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("scripts.compact.subprocess.run", return_value=mock_result):
            with patch("scripts.compact.time.sleep"):
                with pytest.raises(RuntimeError, match="Exhausted"):
                    call_claude_compact("prompt", "content")

    def test_succeeds_on_retry_after_rate_limit(self):
        """A rate-limit followed by success returns the successful output."""
        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stdout = ""
        fail_result.stderr = "rate limit"

        ok_result = MagicMock()
        ok_result.returncode = 0
        ok_result.stdout = "# Summary\n"
        ok_result.stderr = ""

        with patch("scripts.compact.subprocess.run", side_effect=[fail_result, ok_result]) as mock_run:
            with patch("scripts.compact.time.sleep"):
                output = call_claude_compact("prompt", "content")

        assert output == "# Summary\n"
        assert mock_run.call_count == 2

    def test_silent_failure_retries_then_raises(self):
        """rc != 0 with empty stderr is treated as transient (S2.3)."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = ""

        total_attempts = BACKOFF_MAX_RETRIES + 1

        with patch("scripts.compact.subprocess.run", return_value=mock_result) as mock_run:
            with patch("scripts.compact.time.sleep"):
                with pytest.raises(RuntimeError, match="Exhausted"):
                    call_claude_compact("prompt", "content")

        assert mock_run.call_count == total_attempts

    def test_silent_failure_then_success(self):
        """A silent failure followed by success returns the successful output."""
        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stdout = ""
        fail_result.stderr = ""  # empty stderr = silent failure

        ok_result = MagicMock()
        ok_result.returncode = 0
        ok_result.stdout = "# Recovered\n"
        ok_result.stderr = ""

        with patch("scripts.compact.subprocess.run", side_effect=[fail_result, ok_result]) as mock_run:
            with patch("scripts.compact.time.sleep"):
                output = call_claude_compact("prompt", "content")

        assert output == "# Recovered\n"
        assert mock_run.call_count == 2

    def test_silent_failure_with_whitespace_stderr_retries(self):
        """stderr containing only whitespace is also treated as silent."""
        fail_result = MagicMock()
        fail_result.returncode = 2
        fail_result.stdout = ""
        fail_result.stderr = "   \n\t\n"  # whitespace only

        ok_result = MagicMock()
        ok_result.returncode = 0
        ok_result.stdout = "# OK\n"
        ok_result.stderr = ""

        with patch("scripts.compact.subprocess.run", side_effect=[fail_result, ok_result]) as mock_run:
            with patch("scripts.compact.time.sleep"):
                output = call_claude_compact("prompt", "content")
        assert output == "# OK\n"
        assert mock_run.call_count == 2


# ---------------------------------------------------------------------------
# compact_session
# ---------------------------------------------------------------------------

class TestCompactSession:
    def test_dry_run_no_subprocess_no_file_written(
        self, tmp_path, bronze_dir, silver_dir, tmp_manifest, playbook_path
    ):
        """dry_run=True must not call subprocess or write any files."""
        bronze_file = _write_bronze(bronze_dir, "sess-001")
        entry = _make_entry(session_id="sess-001", bronze_path=str(bronze_file))
        _write_manifest(tmp_manifest, [entry])

        with patch("scripts.compact.subprocess.run") as mock_run:
            compact_session(
                entry=entry,
                bronze_dir=bronze_dir,
                silver_dir=silver_dir,
                manifest_path=tmp_manifest,
                playbook_path=playbook_path,
                dry_run=True,
            )

        mock_run.assert_not_called()
        assert list(silver_dir.iterdir()) == []

    def test_writes_silver_file_and_updates_manifest(
        self, bronze_dir, silver_dir, tmp_manifest, playbook_path
    ):
        """Successful compact writes silver file and updates manifest entry."""
        bronze_file = _write_bronze(bronze_dir, "sess-001", "session content x" * 100)
        entry = _make_entry(
            session_id="sess-001",
            bronze_path=str(bronze_file),
            ingested_at="2026-04-17T10:00:00+00:00",
        )
        _write_manifest(tmp_manifest, [entry])

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "# Silver Summary\n\nNear-miss: port 3000 used instead of 3001.\n"
        mock_result.stderr = ""

        with patch("scripts.compact.subprocess.run", return_value=mock_result):
            compact_session(
                entry=entry,
                bronze_dir=bronze_dir,
                silver_dir=silver_dir,
                manifest_path=tmp_manifest,
                playbook_path=playbook_path,
                dry_run=False,
            )

        silver_files = list(silver_dir.glob("*.md"))
        assert len(silver_files) == 1
        assert "sess-001" in silver_files[0].name
        assert "2026-04-17" in silver_files[0].name

        # Verify silver file starts with YAML frontmatter
        silver_content = silver_files[0].read_text(encoding="utf-8")
        assert silver_content.startswith("---\n"), "Silver file must begin with YAML frontmatter"
        assert "session_id:" in silver_content

        entries = read_manifest(str(tmp_manifest))
        updated = next(e for e in entries if e["session_id"] == "sess-001")
        assert updated["status"] == "silver"
        assert updated["silver_path"] == str(silver_files[0])
        assert updated["silver_size_bytes"] > 0
        assert updated["compacted_at"] is not None
        assert updated["error"] is None

    def test_silver_filename_uses_ingested_at_date(
        self, bronze_dir, silver_dir, tmp_manifest, playbook_path
    ):
        """Silver filename uses date from ingested_at field."""
        bronze_file = _write_bronze(bronze_dir, "sess-date-test")
        entry = _make_entry(
            session_id="sess-date-test",
            bronze_path=str(bronze_file),
            ingested_at="2025-12-25T08:00:00+00:00",
        )
        _write_manifest(tmp_manifest, [entry])

        mock_result = MagicMock(returncode=0, stdout="content", stderr="")

        with patch("scripts.compact.subprocess.run", return_value=mock_result):
            compact_session(
                entry=entry,
                bronze_dir=bronze_dir,
                silver_dir=silver_dir,
                manifest_path=tmp_manifest,
                playbook_path=playbook_path,
                dry_run=False,
            )

        silver_files = list(silver_dir.glob("*.md"))
        assert len(silver_files) == 1
        assert silver_files[0].name.startswith("2025-12-25_")


# ---------------------------------------------------------------------------
# main -- idempotency and --session flag
# ---------------------------------------------------------------------------

class TestMain:
    def test_dry_run_no_subprocess(self, tmp_path, bronze_dir, silver_dir, playbook_path, tmp_manifest):
        """main --dry-run must not invoke subprocess.run."""
        bronze_file = _write_bronze(bronze_dir, "s1")
        entry = _make_entry(session_id="s1", bronze_path=str(bronze_file))
        _write_manifest(tmp_manifest, [entry])

        with patch("scripts.compact.subprocess.run") as mock_run:
            rc = main([
                "--dry-run",
                "--manifest", str(tmp_manifest),
                "--silver-dir", str(silver_dir),
                "--prompt", str(playbook_path),
            ])

        assert rc == 0
        mock_run.assert_not_called()

    def test_silver_entries_skipped(self, tmp_path, bronze_dir, silver_dir, playbook_path, tmp_manifest):
        """Sessions already at status silver must be skipped (idempotent)."""
        bronze_file = _write_bronze(bronze_dir, "s-silver")
        entry = _make_entry(session_id="s-silver", bronze_path=str(bronze_file), status="silver")
        _write_manifest(tmp_manifest, [entry])

        with patch("scripts.compact.subprocess.run") as mock_run:
            rc = main([
                "--manifest", str(tmp_manifest),
                "--silver-dir", str(silver_dir),
                "--prompt", str(playbook_path),
            ])

        assert rc == 0
        mock_run.assert_not_called()

    def test_session_flag_processes_only_that_session(
        self, bronze_dir, silver_dir, playbook_path, tmp_manifest
    ):
        """--session processes only the named session, ignoring others."""
        b1 = _write_bronze(bronze_dir, "s1")
        b2 = _write_bronze(bronze_dir, "s2")
        entries = [
            _make_entry(session_id="s1", bronze_path=str(b1)),
            _make_entry(session_id="s2", bronze_path=str(b2)),
        ]
        _write_manifest(tmp_manifest, entries)

        mock_result = MagicMock(returncode=0, stdout="# Summary\n", stderr="")

        with patch("scripts.compact.subprocess.run", return_value=mock_result) as mock_run:
            rc = main([
                "--session", "s1",
                "--manifest", str(tmp_manifest),
                "--silver-dir", str(silver_dir),
                "--prompt", str(playbook_path),
            ])

        assert rc == 0
        # Only one claude call for s1
        assert mock_run.call_count == 1

        manifested = read_manifest(str(tmp_manifest))
        s1 = next(e for e in manifested if e["session_id"] == "s1")
        s2 = next(e for e in manifested if e["session_id"] == "s2")
        assert s1["status"] == "silver"
        assert s2["status"] == "bronze"  # untouched

    def test_session_flag_missing_session_returns_error(
        self, silver_dir, playbook_path, tmp_manifest
    ):
        """--session with unknown session_id returns non-zero exit code."""
        _write_manifest(tmp_manifest, [])
        rc = main([
            "--session", "nonexistent",
            "--manifest", str(tmp_manifest),
            "--silver-dir", str(silver_dir),
            "--prompt", str(playbook_path),
        ])
        assert rc != 0

    def test_failure_sets_status_failed_and_continues(
        self, bronze_dir, silver_dir, playbook_path, tmp_manifest
    ):
        """Per-session exception sets status=failed and continues to next session."""
        b1 = _write_bronze(bronze_dir, "s1")
        b2 = _write_bronze(bronze_dir, "s2")
        entries = [
            _make_entry(session_id="s1", bronze_path=str(b1)),
            _make_entry(session_id="s2", bronze_path=str(b2)),
        ]
        _write_manifest(tmp_manifest, entries)

        fail_result = MagicMock(returncode=1, stdout="", stderr="fatal error")
        ok_result = MagicMock(returncode=0, stdout="# Summary s2\n", stderr="")

        with patch("scripts.compact.subprocess.run", side_effect=[fail_result, ok_result]):
            rc = main([
                "--manifest", str(tmp_manifest),
                "--silver-dir", str(silver_dir),
                "--prompt", str(playbook_path),
            ])

        # Script should still return 0 (errors logged, not fatal)
        assert rc == 0

        manifested = read_manifest(str(tmp_manifest))
        s1 = next(e for e in manifested if e["session_id"] == "s1")
        s2 = next(e for e in manifested if e["session_id"] == "s2")

        assert s1["status"] == "failed"
        assert s1["error"] is not None
        assert s2["status"] == "silver"

    def test_short_and_memory_query_sessions_skipped(
        self, bronze_dir, silver_dir, playbook_path, tmp_manifest
    ):
        """Short and memory_query sessions must not be processed."""
        bs = _write_bronze(bronze_dir, "short-sess")
        bm = _write_bronze(bronze_dir, "mem-sess")
        entries = [
            _make_entry(session_id="short-sess", bronze_path=str(bs), short=True),
            _make_entry(session_id="mem-sess", bronze_path=str(bm), memory_query=True),
        ]
        _write_manifest(tmp_manifest, entries)

        with patch("scripts.compact.subprocess.run") as mock_run:
            rc = main([
                "--manifest", str(tmp_manifest),
                "--silver-dir", str(silver_dir),
                "--prompt", str(playbook_path),
            ])

        assert rc == 0
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# S2.2: part-aware orchestration
# ---------------------------------------------------------------------------

from scripts.compact import (
    _logical_session_id,
    _predecessor_session_id,
    _predecessor_is_silver,
    _silver_filename,
)


class TestLogicalSessionId:
    def test_unsplit_returns_session_id(self):
        e = _make_entry(session_id="abc-123", part_index=None, total_parts=None)
        assert _logical_session_id(e) == "abc-123"

    def test_part_strips_suffix(self):
        e = _make_entry(session_id="abc-123_part_03", part_index=3, total_parts=5)
        assert _logical_session_id(e) == "abc-123"

    def test_subagent_part_strips_suffix(self):
        e = _make_entry(session_id="agent-xyz_part_07", part_index=7, total_parts=10)
        assert _logical_session_id(e) == "agent-xyz"


class TestPredecessorSessionId:
    def test_unsplit_returns_none(self):
        assert _predecessor_session_id(_make_entry(part_index=None)) is None

    def test_part_one_returns_none(self):
        e = _make_entry(session_id="abc_part_01", part_index=1, total_parts=4)
        assert _predecessor_session_id(e) is None

    def test_part_n_returns_n_minus_1(self):
        e = _make_entry(session_id="abc_part_05", part_index=5, total_parts=10)
        assert _predecessor_session_id(e) == "abc_part_04"

    def test_part_format_is_zero_padded(self):
        e = _make_entry(session_id="abc_part_10", part_index=10, total_parts=10)
        assert _predecessor_session_id(e) == "abc_part_09"


class TestPredecessorIsSilver:
    def test_unsplit_always_true(self):
        assert _predecessor_is_silver(_make_entry(part_index=None), []) is True

    def test_part_one_always_true(self):
        e = _make_entry(session_id="abc_part_01", part_index=1, total_parts=3)
        assert _predecessor_is_silver(e, []) is True

    def test_part_two_predecessor_silver(self):
        e = _make_entry(session_id="abc_part_02", part_index=2, total_parts=3)
        manifest = [
            _make_entry(session_id="abc_part_01", part_index=1, total_parts=3, status="silver"),
        ]
        assert _predecessor_is_silver(e, manifest) is True

    def test_part_two_predecessor_bronze(self):
        e = _make_entry(session_id="abc_part_02", part_index=2, total_parts=3)
        manifest = [
            _make_entry(session_id="abc_part_01", part_index=1, total_parts=3, status="bronze"),
        ]
        assert _predecessor_is_silver(e, manifest) is False

    def test_part_two_predecessor_missing(self):
        e = _make_entry(session_id="abc_part_02", part_index=2, total_parts=3)
        assert _predecessor_is_silver(e, []) is False


class TestIsEligibleParts:
    def test_part_with_short_true_still_eligible(self):
        # Chunked parts ignore the short flag.
        e = _make_entry(part_index=2, total_parts=3, short=True, bronze_size_bytes=8000)
        assert is_eligible(e) is True

    def test_part_with_small_size_still_eligible(self):
        # MIN_COMPACT_BYTES doesn't apply to chunked parts either.
        e = _make_entry(part_index=2, total_parts=3, bronze_size_bytes=1000)
        assert is_eligible(e) is True

    def test_unsplit_with_short_still_blocked(self):
        e = _make_entry(part_index=None, short=True)
        assert is_eligible(e) is False


class TestSilverFilenameLogical:
    def test_unsplit_uses_session_id(self):
        e = _make_entry(session_id="abc-123", ingested_at="2026-04-17T10:00:00+00:00")
        assert _silver_filename(e) == "2026-04-17_abc-123.md"

    def test_part_uses_logical_session_id(self):
        e = _make_entry(
            session_id="abc-123_part_05",
            part_index=5, total_parts=10,
            ingested_at="2026-04-17T10:00:00+00:00",
        )
        # All parts collapse to the same filename via the logical id.
        assert _silver_filename(e) == "2026-04-17_abc-123.md"

    def test_all_parts_share_same_filename(self):
        ingested = "2026-04-17T10:00:00+00:00"
        names = {
            _silver_filename(_make_entry(
                session_id=f"abc-123_part_{i:02d}",
                part_index=i, total_parts=5, ingested_at=ingested,
            ))
            for i in range(1, 6)
        }
        assert names == {"2026-04-17_abc-123.md"}


class TestCompactSessionContinuation:
    """End-to-end compact_session tests for chunked sessions (mocked claude)."""

    def test_part_one_writes_fresh_silver_with_frontmatter(
        self, tmp_path, bronze_dir, silver_dir, tmp_manifest, playbook_path
    ):
        bronze_file = _write_bronze(bronze_dir, "abc_part_01", content="bronze part 1")
        entry = _make_entry(
            session_id="abc_part_01",
            part_index=1, total_parts=3, parent_session="abc",
            bronze_path=str(bronze_file),
        )
        _write_manifest(tmp_manifest, [entry])

        with patch("scripts.compact.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="# Session: test\n## Trajectory\n\n### 1. step\n", stderr="")
            compact_session(
                entry=entry, bronze_dir=bronze_dir, silver_dir=silver_dir,
                manifest_path=tmp_manifest, playbook_path=playbook_path, dry_run=False,
            )

        silver_file = silver_dir / "2026-04-17_abc.md"
        assert silver_file.exists()
        content = silver_file.read_text()
        # Frontmatter present
        assert content.startswith("---\n")
        # Logical session id used in frontmatter, not part-suffixed
        assert "session_id: abc" in content
        assert "session_id: abc_part_01" not in content
        # Manifest updated with offset 0 for part 1
        from scripts.manifest import read_manifest as _rm
        m = _rm(str(tmp_manifest))
        assert m[0]["status"] == "silver"
        assert m[0]["silver_offset_bytes"] == 0

    def test_part_n_appends_with_continuation_prompt(
        self, tmp_path, bronze_dir, silver_dir, tmp_manifest, playbook_path
    ):
        # Pre-populate silver from part 1.
        silver_dir.mkdir(parents=True, exist_ok=True)
        silver_file = silver_dir / "2026-04-17_abc.md"
        prior = "---\nsession_id: abc\n---\n\n# Session: test\n## Trajectory\n\n### 1. first step\n"
        silver_file.write_text(prior, encoding="utf-8")
        prior_size = silver_file.stat().st_size

        # Continue prompt file
        cont_path = tmp_path / "continue.md"
        cont_path.write_text("CONTINUE PROMPT", encoding="utf-8")

        bronze_file = _write_bronze(bronze_dir, "abc_part_02", content="bronze part 2")
        entry = _make_entry(
            session_id="abc_part_02",
            part_index=2, total_parts=3, parent_session="abc",
            bronze_path=str(bronze_file),
        )
        # Part 1 is already silver in manifest
        part1 = _make_entry(
            session_id="abc_part_01", part_index=1, total_parts=3,
            parent_session="abc", status="silver",
        )
        _write_manifest(tmp_manifest, [part1, entry])

        with patch("scripts.compact.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="### 2. second step\n", stderr="")
            compact_session(
                entry=entry, bronze_dir=bronze_dir, silver_dir=silver_dir,
                manifest_path=tmp_manifest, playbook_path=playbook_path,
                continue_playbook_path=cont_path, dry_run=False,
            )

        # Verify continuation prompt was used and inputs wrapped
        call_args = mock_run.call_args[0][0]  # the cmd list
        combined = call_args[-1]  # last positional arg is the combined prompt + input
        assert "CONTINUE PROMPT" in combined
        assert "<prior-silver>" in combined
        assert "<bronze-part>" in combined
        assert "part_index=2 total_parts=3" in combined

        # Silver appended (prior content preserved)
        new_content = silver_file.read_text()
        assert prior in new_content
        assert "### 2. second step" in new_content

        # Manifest updated with part 2's offset = file size before append
        from scripts.manifest import read_manifest as _rm
        m = _rm(str(tmp_manifest))
        part2 = next(e for e in m if e["session_id"] == "abc_part_02")
        assert part2["status"] == "silver"
        assert part2["silver_offset_bytes"] == prior_size
