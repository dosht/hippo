# Reconcile Playbook

## Purpose

Cross-reference new gold entries with existing ones. Detect confirmations, contradictions, extensions, and obsolescence. Update confidence scores based on feedback. Identify high-frequency topics for promotion.

## When to Run

- Nightly via Claude Code Routine (cloud, automated)
- After a batch of new gold entries have been created
- Only meaningful when there are existing gold entries to compare against

## Steps

### 1. Identify New Entries

Find gold entries created since the last reconciliation run. Check manifest for sessions with status "gold" but not yet "reconciled".

### 2. For Each New Entry, Compare with Existing

For each new gold entry:

a. Run `qmd query "{entry topics and title}" --collection hippo --json -n 5` to find related existing entries.

b. Read the new entry and each related existing entry.

c. Determine the relationship:
   - **Confirms**: New entry says the same thing. Update `last_validated` on the existing entry.
   - **Contradicts**: New entry says something different. Lower confidence on the older entry. Add a note about the contradiction. Prefer the newer information unless the older entry has `confidence: verified`.
   - **Extends**: New entry adds information to the same topic. Merge the new details into the existing entry (or keep as separate entry if the extension is substantial).
   - **Obsoletes**: New entry explicitly supersedes old information. Mark the old entry's `supersedes` field. Lower old entry's confidence.
   - **Novel**: No related entries found. The new entry stands alone.

### 3. Process Feedback Log

Read `feedback.jsonl` for entries not yet processed:

- Positive feedback (`useful: true`): Increment `query_count` on the entry.
- Negative feedback (`useful: false`): Lower confidence. If the `note` field explains why, use that as a correction signal.
- If an entry has 3+ negative feedbacks without positive ones, flag for human review.

### 4. Check for Promotion Candidates

Identify entries where `query_count` exceeds a threshold (e.g., 10). These are candidates for promotion to:
- **CLAUDE.md** entry (always loaded, zero-cost retrieval)
- **Dedicated skill** (for complex procedural knowledge)

Write suggestions to `gold/suggestions/promote-<entry-id>.md`.

### 5. Check for Staleness

Find entries where `last_validated + staleness_policy < today`. Flag them with a note in the entry's frontmatter or in a staleness report.

### 6. Update Manifest

Mark reconciled sessions as `status: "reconciled"`.

### 7. Git Commit

```bash
git add gold/ manifest.jsonl feedback.jsonl
git commit -m "reconciliation: <date> - <summary of changes>"
git push
```

### 8. Reindex QMD

```bash
qmd update && qmd embed
```

## Reconciliation Report

After each run, produce a brief summary:
- Entries confirmed: N
- Entries updated/extended: N
- Contradictions found: N
- New entries: N
- Promotion candidates: N
- Stale entries flagged: N

## Notes

- Reconciliation is the most complex step. Start conservative: prefer creating new entries over modifying existing ones. Manual review can clean up later.
- The reconciliation agent should use the memory subagent itself (eat your own dog food). This creates a natural feedback loop.
- Contradictions are the hardest case. Default to flagging for human review rather than auto-resolving. Time-based ("newer wins") is a heuristic, not a rule.
