---
title: Known-Failures Registry for Test Runs
type: pattern
status: proposed
created: 2026-04-29
source: MVP-2-01 retrospective
---

# Known-Failures Registry

## Problem

When a test suite carries pre-existing failures unrelated to the current story,
every reviewer in a multi-agent workflow re-derives "are these mine?" from
scratch. During MVP-2-01, six `TestCallClaudeCompact` retry-backoff failures
appeared in every test run; the developer, tester, tech-lead, and architect each
had to reason about them independently. This is wasted signal and wasted tokens.

## Pattern

Maintain a single registry file at `.claude-team/known-failures.md` (or
`.claude-team/known-failures.json` for machine parsing). Each entry is one line:

```
test_id | reason | tracking_ref | added_on
```

Example:

```
tests/test_compact.py::TestCallClaudeCompact::test_retry_backoff_exhausted | retry-backoff tests assert RuntimeError but impl now raises QuotaExhaustedError | BUGS-?? | 2026-04-29
```

The `run-tests` playbook instructs the developer agent to:

1. Load the registry on start.
2. Filter the listed test IDs out of the pass/fail tally.
3. Report results in the form: `N in-scope passed, M in-scope failed, K known
   failures unchanged (registry: .claude-team/known-failures.md)`.

Reviewers then receive a single clean signal instead of having to read past
the noise.

## Side benefits

- The registry doubles as a backlog: once it grows past a small threshold (say
  six entries), file a story to drain it. Treat it like a `// XXX` ledger.
- New regressions surface immediately as "in-scope failed" instead of being
  diluted by chronic noise.
- Diff-able history: when a known failure is fixed, the registry entry is
  removed in the same commit.

## When to file an entry

- The failure is **unrelated** to the current story's scope.
- The failure existed **before** the story's branch began (verify with `git
  log` or by checking out the parent commit).
- The fix is **deferred**, with a tracking reference (issue/story ID).

If the fix is not deferred, fix it in the current story instead of registering
it.

## When NOT to use this pattern

- For flaky tests (intermittent failures): use a separate flake registry, not
  this one. Known failures are deterministic.
- For genuinely deprecated tests: delete them.
- For environment-specific failures (works locally, fails in CI): fix the
  environment mismatch, do not register.

## Status

Proposed pattern. Not yet implemented in any project's `run-tests` playbook.
First adopter should also update the developer agent's testing guide and
include a registry file template.
