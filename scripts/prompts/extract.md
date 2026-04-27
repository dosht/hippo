You are extracting reusable knowledge entries from a compacted coding session.

## Input shape

The session is a **chronological trajectory** of `(action, response, [adjustment])` events. Each event is a numbered step with `**User:**`, `**Action:**`, `**Response:**`, and optionally `**Adjustment:**` lines. A `**User:**` line means human-driven; an `**Adjustment:**` line means environment-driven (tool error, test failure, parser mismatch — the agent recovered within the same step).

Concrete values (paths, error messages, command flags, port numbers, file:line refs, env vars) are inline within steps — pull them into the gold entry verbatim. Heuristics often emerge from **near-miss arcs** (step's full Action → Response → Adjustment sequence): the specific failure reason and the specific fix together encode the lesson. A near-miss arc is a strong signal that this step deserves a gold entry.

Read the trajectory holistically — heuristics may span multiple steps (e.g. a debugging arc resolved across 3 steps). Don't try to mint a heuristic from a single step if the lesson only makes sense with neighboring context.

## Decide which knowledge becomes gold

For each distinct piece of knowledge in the session, decide if it should become a gold entry.

A good gold entry is:
- REUSABLE: Would help a future agent working on a similar task
- CONCRETE: Contains specific commands, ports, paths, configurations
- NON-OBVIOUS: Not something a well-trained LLM would already know
- OPERATIONAL: About how things actually work in practice, not theory

NOT gold-worthy:
- General programming knowledge ("use async/await for I/O")
- Project-specific implementation details that only matter for one story
- Temporary workarounds that will be obsolete next week
- Information already in the project's curated docs

For each entry, you decide seven things:
1. **id** — kebab-case slug, prefixed `mem-`. Example: `mem-stripe-webhook-raw-body`.
2. **type** — one of `operational`, `pattern`, `gotcha`, `decision-context`.
3. **topics** — list of short tags (3–6). Example: `[stripe, webhooks, signature-verification]`.
4. **summary** — one sentence, ≤140 characters, capturing the core insight. Must not be a restatement of the title. Example: `"Use port 9224 not 9222 because AI Browser runs a separate Chrome instance to avoid profile conflicts."` Place this field adjacent to `topics:` in the frontmatter.
5. **projects** — `[all]` if the knowledge applies universally across projects, otherwise a single-item list like `[kenznote-payments]`. Use the `project` from the session metadata as the project name when scoping to a project.
6. **agents** — `[all]` if any agent type can use it, otherwise a list like `[developer]` or `[tech-lead]`. Use the `agent` from session metadata when scoping to one.
7. **staleness_policy** — `30d` (volatile, e.g. tool versions / APIs), `60d`, `90d` (more stable patterns), or `never` (decisions, architectural facts).

Plus the **title** and **body** (markdown, concrete details with paths/commands/errors inline).

Other fields in the gold YAML frontmatter (`source_sessions`, `created`, `last_validated`, `last_queried`, `query_count`, `supersedes`, `confidence`) are filled in deterministically by the orchestration script. **Do not emit them in your output** — they will be discarded if you do.

If no gold-worthy knowledge exists in this session, output exactly the text `NO_NEW_KNOWLEDGE` and stop.

## Session metadata (injected above the silver content)

```
session_id: <uuid>
project: <name>
agent: <agent type or null>
agent_task: <optional, subagent sessions only>
git_branch: <optional>
session_started_at: <optional ISO8601>
```

Use `project` and `agent` for the `projects` and `agents` frontmatter fields you decide. The other metadata fields are context for understanding the session — reference them in the entry body if it helps locate context (e.g. "discovered on the payments branch"), but do not put them in frontmatter.

## Output format

Emit one or more entries using exactly this format. The orchestration script parses the YAML between `---` markers and the body between the second `---` and `===ENTRY END===`.

```
===ENTRY START===
---
id: mem-kebab-case-id
type: operational
topics: [tag1, tag2, tag3]
summary: One sentence ≤140 chars capturing the core insight, not just the title rephrased.
projects: [project-name-or-all]
agents: [all]
staleness_policy: 90d
---

# Entry Title

Entry body in markdown. Be concrete and specific. Pull paths, error messages,
ports, file:line refs, command flags from the trajectory verbatim.

===ENTRY END===
```

If no gold-worthy knowledge exists, output **only** the literal text:

```
NO_NEW_KNOWLEDGE
```

QUALITY BAR:
- Prefer fewer, higher-quality entries over many low-quality ones.
- Each entry should be self-contained. A reader should understand it without knowing the original session.
- If the session was a debugging journey with a resolution, the gold entry is the resolution
  (with enough context to understand when it applies), not the full debugging narrative.
- Include the "why" not just the "what". "Use port 9333" is less useful than
  "Use port 9333 because the AI Browser is configured separately from the system Chrome to avoid profile conflicts."
- Memory-query sessions (agent: "memory") should NOT produce gold entries.
