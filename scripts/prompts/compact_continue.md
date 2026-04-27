You are compacting one part of a Claude Code session that was split into multiple parts. The earlier parts have already been compacted into a silver trajectory; you are extending that trajectory with the next part. Your output is appended verbatim to the existing silver file — so it must NOT contain a header, preamble, or any restated content.

## Hard output rules (read first, apply throughout)

These are non-negotiable. Violating any of them produces unusable silver.

1. **No preamble.** Do NOT begin with "Continuing from the prior part…", "Here are the next steps…", or any other meta-commentary. Your VERY FIRST output line is the next `### N.` step heading, where N continues the numbering from prior silver.
2. **No closing remarks.** Do NOT end with a `**Note:** ...`, `**Summary:** ...`, "Session ended mid-execution", or any reflection on the session as a whole. Your LAST output line is the final `### N.` step's last labeled line (`**Response:**` or `**Adjustment:**`).
3. **No new section headings.** Do NOT add `## Decisions`, `## Problems`, `## Configurations`, `## Near-Misses`, `## Open Questions`, `## Summary`. The prior silver already has the `## Trajectory` heading; you only emit `### N. ...` step blocks.
4. **No `---` horizontal rules** anywhere.
5. **No restating prior silver.** The reader already has it. Do NOT recap, paraphrase, or reference earlier steps in your output.
6. **Output is appended verbatim.** Anything you write that violates these rules ships into the artifact.

## What silver is

Silver is a **chronological narrative** of what happened in the session, told as a sequence of `(action, environment response, adjustment)` events in time order. It is NOT a themed summary, NOT a list of decisions, NOT a categorized post-mortem. Think of it as the session's replay log, denoised.

The reader (a future agent or the gold-extraction stage) needs to see causality: what was attempted → what came back → how the agent responded. That cause-and-effect chain is the unit of learning. Preserve it.

## Inputs you receive

- `<prior-silver>...</prior-silver>` — the silver content for parts 1..N-1, already in the trajectory shape described below. **Read-only context.** You will need to read it to know the last step number and to maintain causal continuity at the part boundary, but you must not re-emit any of it and must not edit it.
- `<bronze-part>...</bronze-part>` — the JSONL bronze content for part N. This is your input to compact.
- `<part-info>part_index=N total_parts=M</part-info>` — for awareness only; not for output.

## Output rules

Output **only the next set of steps** in the trajectory, in the format the prior silver already uses:

```
### N. <terse action label>
**User:** <quote or paraphrase of the user's request, with concrete details>
**Action:** <what the agent did — tool call, edit, command, decision — with concrete values: file paths, command flags, port numbers, env vars>
**Response:** <what the environment returned — tool output, error, test result, user reaction. Quote error messages verbatim.>
**Adjustment:** <only if the agent then corrected; describe how. Omit this line entirely if not applicable.>

### N+1. <next action label>
...
```

- **Continue the step numbering** from where prior silver left off. If prior silver's last step was `### 27. ...`, your first step is `### 28. ...`.
- **No `# Session:` header, no front-matter, no `## Trajectory` heading.** The prior silver already has those.
- **No preamble** ("Continuing from the prior part…", "Here are the next steps…"). Output the first new `### N.` block directly.
- **No closing summary, no "Open Questions" section.**
- If part N's bronze contains the environment response or adjustment for an action that started in part N-1, narrate it as the next step's `**Response:**` and `**Adjustment:**`. Do not retroactively edit prior silver; the reader stitches the arc by reading both files in order.

## What to preserve

- **Concrete values** at the point they occurred: file paths, command flags, port numbers, environment variables, error messages (verbatim), file:line references, version strings, config keys. Keep them inside the step where they happened. Do NOT collect them into a list.
- **Near-miss failures** — attempts that almost worked but failed for a specific, non-obvious reason. These are the highest-value events. Render the full arc as one step: Action → Response (the specific failure) → Adjustment (what fixed it).
- **User corrections** — when the user said "no, try X instead" or "that's wrong because Y". A user correction always **starts a new step**, with the user's pushback as the new step's `**User:**` line. Quote it verbatim.
- **Tool errors and recoveries** — every error is a reward signal. Don't drop them. Quote the error message verbatim.

### Adjustment field: environment-driven only

`**Adjustment:**` is reserved for **environment-feedback-driven recoveries within the same step** — the agent retried after a tool error, switched a flag after a parser mismatch, fixed a path after a missing-file error, etc. The trigger is always something the environment returned (tool output, error, test result), not something a human said.

**User-driven changes never appear as `**Adjustment:**`.** When the user pushes back, that becomes the **next step's `**User:**` line**, and the agent's response is the new step's `**Action:**`. A `**User:**` line means human-driven; an `**Adjustment:**` line means environment-driven.
- **Decisions and the reason for them** — capture both in the same step.

### Worked example of a near-miss step

```
### 12. Wire LemonSqueezy webhook
**User:** Add the webhook handler for LS subscription.created
**Action:** Added `app/routes/api.webhooks.lemonsqueezy.ts` with `crypto.createHmac('sha256', WEBHOOK_SECRET)` signature check, then ran `curl -X POST http://localhost:3000/api/webhooks/lemonsqueezy -d @fixtures/lemonsqueezy.subscription_created.json`
**Response:** `401 Unauthorized — Invalid signature`. Stripe-style hex digest didn't match LS expected signature.
**Adjustment:** LS signs the **raw body bytes** before any parsing; switched from `JSON.stringify(req.body)` to `await req.text()`, recomputed the HMAC against that string. Webhook returned `200 OK` and inserted into `webhook_events`.
```

This is a near-miss arc: the action almost worked, the specific failure (`401 — Invalid signature`) is named, and the recovery (`raw body bytes` not `JSON.stringify`) is concrete. The whole arc lives in one step.

## What to remove

- Full file contents from Read tool outputs — keep just the filename and a one-line summary of what was in it.
- Routine command outputs that worked as expected with no surprise.
- Boilerplate ("Sure, I'll help with that", "Let me think about this").
- Directory listings and file-existence checks.
- Repeated identical attempts — collapse to "tried X N times, all failed because Y" — UNLESS one of them is a near-miss with distinct detail, in which case keep that one.

## What NOT to do

- **Do not collapse near-misses into "tried X, failed".** The specific failure reason is the teaching content.
- **Do not re-emit any content from `<prior-silver>`.** The reader already has it.
- **Do not edit the prior silver.** Move forward in time only.
- **Do not invent context.** If `<bronze-part>` doesn't say why something was done, don't speculate.
- **Do not stop short** with a closing note explaining the session was incomplete. The absence of a resolution IS the signal — let the trajectory end at the last actual step.

## Quality bar

- A reader concatenating prior silver + your output should be able to follow the session in order with no break in causality.
- Concrete values appear inline at the moment they happened.
- Near-miss arcs read as cause → effect → recovery within a single step.
- Aim for similar size reduction as prior silver implies (60–80% vs the bronze part). Less than 40% may indicate dense content; more than 90% suggests detail was lost.
