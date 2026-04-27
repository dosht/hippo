---
name: hippo-remember
description: "Query the Hippo experiential memory system when you need operational knowledge from past coding sessions. Use when encountering unfamiliar tools, configurations, ports, deployment patterns, debugging approaches, or any operational detail that might have been solved before in a previous session. Do NOT use for curated docs (architecture, conventions, story requirements)."
---

# Hippo Remember

Hippo is an experiential memory system that captures knowledge from past AI coding sessions automatically. It is queryable from any project.

## Architecture (do not bypass)

You delegate to a **dedicated memory subagent** that runs in its own context window. The subagent does the qmd search, reads the matching gold entries, and returns a concise synthesized answer (~200 words). You do **not** run qmd yourself, do **not** read gold entry files yourself — that pollutes your context with every query. Let the subagent's context absorb the search noise.

This is the load-bearing design choice (see `docs/brainstorm/session-summary.md` and the **ERL** finding "LLM-based retrieval outperforms embedding-only"): parent stays clean, subagent does the retrieval and synthesis.

## How to Query

Spawn the memory subagent via `claude -p`, **launched from `~/src/hippo`** (or `$HIPPO_HOME` if set) so it has the gold/ directory and qmd config in its working tree:

```bash
HIPPO_DIR="${HIPPO_HOME:-$HOME/src/hippo}"
(cd "$HIPPO_DIR" && claude -p "{user's question, verbatim}" \
  --append-system-prompt "$(cat <<'PROMPT'
You are the Hippo memory retrieval subagent. Your only job: search Hippo gold entries and return a concise, grounded answer.

Procedure:
1. Run: qmd query "{the question, possibly clarified}" --collection hippo --json -n 5
2. From results, take entries with score >= 0.5. Read the top 1-3 markdown files at gold/entries/<entry-id>.md (use the file basename from the qmd `file` field). Snippets alone are not enough.
3. If the user is clearly working in a specific project (cwd context, project name in question), prefer entries whose frontmatter `projects:` includes that project or `[all]`. Don't be strict — cross-project knowledge often applies.
4. Synthesize a concise answer (under 200 words) using ONLY information from those entry files. Inline the concrete details (paths, commands, ports, error messages) — research shows agents ignore abstract summaries but follow concrete details.
5. End your answer with one line each:
   - Source: <entry-id>[, <entry-id>]
   - Confidence: <high|medium|low> from frontmatter
   - Last validated: <YYYY-MM-DD> from frontmatter
   - Stale warning: only if last_validated is older than the entry's staleness_policy

If no result above 0.5 or all results are clearly off-topic, reply exactly: "No relevant entries found in Hippo memory." Do not invent answers. Do not fall back to general knowledge.

Rules:
- Never speculate beyond gold entry content.
- Do not spawn further sub-claude calls. Run qmd and Read directly.
- Do not cd. Use absolute paths if needed.
PROMPT
)" \
  --model haiku \
  --max-turns 5)
```

Pass the user's question verbatim where shown. Add light clarification if it improves search (e.g. expand "auth" to "authentication" if the question is otherwise ambiguous).

After the subagent returns, surface its answer to the user. Do not re-do the search yourself, do not second-guess the answer unless it is clearly wrong (e.g. cites a tool the user has never used).

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
