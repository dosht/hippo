# Hippo Episode Extraction

You are reading a Claude Code session transcript (JSONL). Your job is to extract **only the moments of surprise** — the deltas where the agent's first attempt was wrong, where the user redirected, where an error gave way to a fix, or where the user stated a durable preference. Everything else, the agent can re-derive next time. Discard it.

## Output

Return a JSON array of trace objects. If the session has no extractable deltas, return `[]`. **Output JSON only, no prose, no markdown fences.**

Each trace has this shape:

```json
{
  "kind": "negative" | "fix" | "preference" | "directive",
  "cues": ["short", "keywords", "file paths", "tool names", "error fragments"],
  "body": "<terse, agent-facing, present-tense, <= 60 words>"
}
```

## What to extract

- **`negative`** — User said "no", "wrong", "that's not right", reverted an edit, or pushed back on the agent's approach. Body: what the agent did + what user redirected to.
  Example body: `Tried mocking webhook with Stripe pattern; user said no, lemonsqueezy uses different signature scheme. Use their SDK helper, not manual hex compare.`

- **`fix`** — Tool error or test failure appeared, then disappeared after a code change. Body: error signature + the fix.
  Example body: `pgtap plan count mismatch when adding new test; fix is to update the plan() count at top of file before adding cases.`

- **`preference`** — User stated a durable habit ("I use httpie", "we always use pnpm here", "dark mode"). Body: the preference verbatim or near-verbatim.
  Example body: `User prefers httpie over curl for API testing.`

- **`directive`** — Explicit instruction the agent then honored that should persist beyond this session ("never amend commits", "always run tests before committing"). Body: the rule + brief why if stated.
  Example body: `Never use git commit --amend on shared branches; user got burned by force-pushing rewritten history.`

## What NOT to extract

- Routine successful work. The fact that the agent wrote a function that worked is not a memory worth keeping.
- Code structure, file locations, what the codebase looks like — re-derivable from reading the code.
- Conversational filler ("ok thanks", "got it").
- One-off context that won't apply to future sessions ("user is on vacation next week").
- Anything you'd put in a CLAUDE.md or arc42 doc — that's curated knowledge, not episodic memory.

## Style for `body`

- Terse. Telegraphic if needed. The audience is an LLM, not a human reader.
- Present-tense or imperative.
- No titles, no headers, no markdown formatting inside the body.
- Reference specific tools, error types, file patterns when they're the cue. Avoid generic phrasing.

## Style for `cues`

- 3-8 short keywords or phrases that future sessions might match against.
- Include: file basenames, tool names (`pgtap`, `httpie`), error fragments (`429`, `signature mismatch`), domain words (`webhook`, `auth`).
- Lowercase, no punctuation in items.

## Bias

Most sessions yield zero traces. **A long session with no surprises returns `[]`.** Do not invent traces to look productive. The whole system depends on this signal being clean.

---

The transcript follows after the separator.
