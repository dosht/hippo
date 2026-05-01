---
name: hippo-remember
description: "Query the Hippo experiential memory system when you need operational knowledge from past coding sessions. Use when encountering unfamiliar tools, configurations, ports, deployment patterns, debugging approaches, or any operational detail that might have been solved before in a previous session. Do NOT use for curated docs (architecture, conventions, story requirements)."
---

# Hippo Remember

Hippo is an experiential memory system that captures knowledge from past AI coding sessions automatically. It is queryable from any project.

## How to Query

Run the wrapper script with the user's question as a single argument:

```bash
~/.claude/skills/hippo-remember/bin/hippo-remember "<the user's question, verbatim>"
```

Surface stdout to the user as the answer. That is the entire interface.

## Hard Rules — Do Not Bypass

The script encapsulates a memory retrieval subagent that runs in its own context window, searches the gold corpus, and synthesizes a concise answer. You do **not** need to know how it works internally, and you **must not** reproduce its work.

If the script exits non-zero, errors, is denied permission, or is otherwise unavailable:

1. Surface the script's stderr to the user verbatim.
2. **STOP.**
3. Do **not** run `qmd` directly.
4. Do **not** read any files under `gold/entries/` or `$HIPPO_HOME`.
5. Do **not** `cd` into the Hippo project directory.
6. Do **not** invent a fallback retrieval procedure of any kind.

These fallbacks defeat the entire point of the abstraction: keeping the parent agent's context clean of search-result noise, gold-entry text, and implementation details. A failed query returning honestly to the user is correct behavior. An "improvised" query that pollutes the parent's context is a bug.

## When to Use

- "What port does AI Browser use for CDP?" (operational detail from a past session)
- "How did we handle Supabase RLS for file uploads?" (pattern from experience)
- "What's the gotcha with Railway deployments and PORT env?" (known gotcha)
- "Why did we choose LemonSqueezy over Stripe?" (decision context)

## When NOT to Use

- Questions answered by the project's developer-guide or architecture docs
- Questions about story requirements or acceptance criteria
- General programming knowledge (you already know this)
- Questions about things that change rapidly (use web search instead)

## Giving Feedback

If a memory answer was helpful or wrong, note it in the session. The nightly pipeline will pick up the feedback from the session transcript and adjust confidence scores (planned post-MVP).
