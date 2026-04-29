"""
scripts/errors.py -- Shared error types for the Hippo pipeline.

Single source of truth for QuotaExhaustedError and its detection
heuristics. Imported by compact.py, extract.py, and reconcile.py.
"""
from __future__ import annotations


class QuotaExhaustedError(RuntimeError):
    """Raised when claude -p indicates quota/rate-limit exhaustion.

    Treated as a graceful stop signal: the current run aborts without marking
    the in-flight entry as failed, so the next scheduled run picks it up.

    Detection heuristics (applied by callers, not here):
      - HTTP 429 exit code
      - "rate limit" or "429" substring in stderr (case-insensitive)
      - Non-zero exit code with empty stderr (silent failure / network blip)
    """


def is_rate_limit_error(returncode: int, stderr: str) -> bool:
    """Return True if the subprocess failure looks like a rate-limit error."""
    if returncode == 429:
        return True
    lowered = stderr.lower()
    return "429" in lowered or "rate limit" in lowered


def is_transient_silent_failure(returncode: int, stderr: str) -> bool:
    """Return True if claude exited non-zero with no stderr (likely transient).

    Observed in prior runs: claude returns a non-zero rc but writes nothing to
    stderr. Root cause is unclear but a transient retry is the correct general
    response -- if the underlying issue is persistent the retry will exhaust and
    surface as failed.
    """
    return returncode != 0 and stderr.strip() == ""
