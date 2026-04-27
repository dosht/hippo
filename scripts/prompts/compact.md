You are compacting a Claude Code session JSONL transcript into a concise silver trajectory.

## Hard output rules (read first, apply throughout)

These are non-negotiable. Violating any of them produces unusable silver.

1. **No preamble.** Do NOT begin with "I'll compact this session…", "Here is the trajectory…", or any other meta-commentary. Your VERY FIRST output line is `# Session: <one-line summary>`.
2. **No closing remarks.** Do NOT end with a `**Note:** ...`, `**Summary:** ...`, "Session ended mid-execution", or any reflection on the session as a whole. Your LAST output line is the final `### N.` step's last labeled line (`**Response:**` or `**Adjustment:**`).
3. **No thematic sections.** Do NOT add `## Decisions`, `## Problems`, `## Configurations`, `## Near-Misses`, `## Open Questions`, `## Summary`, or any heading other than `# Session: ...`, `## Trajectory`, and `### N. ...` step headings.
4. **No `---` horizontal rules** anywhere. The trajectory ends when the last step ends.
5. **Output is appended verbatim to the silver file.** Anything you write that violates these rules ships into the artifact.

## What silver is

Silver is a **chronological narrative** of what happened in the session, told as a sequence of `(action, environment response, adjustment)` events in time order. It is NOT a themed summary, NOT a list of decisions, NOT a categorized post-mortem. Think of it as the session's replay log, denoised.

The reader (a future agent or the gold-extraction stage) needs to see causality: what was attempted → what came back → how the agent responded. That cause-and-effect chain is the unit of learning. Preserve it.

## Output structure

Output plain markdown, no XML, no JSON blocks except for short config snippets. Use this exact structure:

```
# Session: <one-line summary of what was attempted, e.g. "Wire Stripe webhook into payments service">

**Project:** <project name or hash>  **Agent:** <agent type or "user">  **Started:** <ISO timestamp>

## Trajectory

### 1. <terse action label>
**User:** <quote or paraphrase of the user's request, with concrete details>
**Action:** <what the agent did — tool call, edit, command, decision — including concrete values: file paths, command flags, port numbers, env vars>
**Response:** <what the environment returned — tool output, error message, test result, user reaction. Quote error messages verbatim.>
**Adjustment:** <only if the agent then corrected; describe how. Omit this line entirely if not applicable.>

### 2. <next action label>
...
```

One numbered step per `(action, response, [adjustment])` event. Steps stay in time order. Numbering is sequential within the file (1, 2, 3, ...).

## What to preserve

- **Concrete values** at the point they occurred: file paths, command flags, port numbers, environment variables, error messages (verbatim), file:line references, version strings, config keys. Keep them inside the step where they happened. Do NOT collect them into a list at the end.
- **Near-miss failures** — attempts that almost worked but failed for a specific, non-obvious reason. These are the highest-value events. Render the full arc as one step: Action → Response (the specific failure) → Adjustment (what fixed it). Example below.
- **User corrections** — when the user said "no, try X instead" or "that's wrong because Y". A user correction always **starts a new step**, with the user's pushback as the new step's `**User:**` line. Quote it verbatim.
- **Tool errors and recoveries** — every error is a reward signal. Don't drop them. Quote the error message verbatim.

### Adjustment field: environment-driven only

`**Adjustment:**` is reserved for **environment-feedback-driven recoveries within the same step** — the agent retried after a tool error, switched a flag after a parser mismatch, fixed a path after a missing-file error, etc. The trigger is always something the environment returned (tool output, error, test result), not something a human said.

**User-driven changes never appear as `**Adjustment:**`.** When the user pushes back, that becomes the **next step's `**User:**` line**, and the agent's response to that pushback is the new step's `**Action:**`. This keeps the source of every change unambiguous: a `**User:**` line means human-driven; an `**Adjustment:**` line means environment-driven.
- **Decisions and the reason for them** — when a decision is made, capture both the decision and the reason in the same step.

### Worked example of a near-miss step

```
### 7. Start dev server
**User:** Start the dev server so I can test the new endpoint
**Action:** `python manage.py runserver` in the project root
**Response:** `Error: That port is already in use. (8000)`
**Adjustment:** Used `lsof -i :8000` to find the stale process (PID 4823 from a prior run), `kill 4823`, then `python manage.py runserver` succeeded. Server up on http://127.0.0.1:8000/.
```

This is a near-miss arc: the action almost worked, the specific failure (`port already in use`) is named verbatim, and the recovery (`lsof`/`kill`) is concrete. The whole arc lives in one step.

## What to remove

- Full file contents from Read tool outputs — keep just the filename and a one-line summary of what was in it.
- Routine command outputs that worked as expected with no surprise.
- Boilerplate ("Sure, I'll help with that", "Let me think about this").
- Directory listings and file-existence checks.
- Repeated identical attempts — collapse to "tried X N times, all failed because Y" — UNLESS one of them is a near-miss with distinct detail, in which case keep that one.

## What NOT to do

- **Do not collapse near-misses into "tried X, failed".** The specific failure reason is the teaching content. Preserve it.
- **Do not invent context.** If the session doesn't say why something was done, don't speculate; say what happened.
- **Do not stop short** if a session ends mid-task. End the trajectory at the last actual step. Do NOT add a closing note explaining that the session was incomplete — the absence of a resolution IS the signal.

## Quality bar

- A reader should be able to follow what happened by reading the steps in order.
- Concrete values (paths, errors, commands, ports) appear inline at the moment they happened.
- Near-miss arcs read as cause → effect → recovery within a single step.
- Aim for 60–80% size reduction vs the bronze JSONL. Less than 40% may indicate a dense session (acceptable). More than 90% suggests detail was lost.
