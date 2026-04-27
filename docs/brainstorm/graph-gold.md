# Graph-Shaped Gold (Obsidian-Vault Style)

**Status:** Brainstorm / deferred. Captured 2026-04-26 during MVP-1 hackathon (M3 reconcile design).

**Tl;dr:** Should gold entries form a graph (typed edges between entries), not just a flat collection of heuristics? Real value, but premature for MVP-1. Revisit after extract + reconcile produce enough corpus to know what edges actually matter.

## Why it came up

While designing the reconcile stage (M4 in `hackathon-brief.md`), the `related` classification (pair of entries that aren't duplicates and aren't contradictions, but reference each other) implied a cross-link. That naturally raised: should gold be a graph, like an Obsidian vault?

## Why it's interesting for Hippo specifically

The RL framing already implies graph structure:

- A `(action, reward, adjustment)` chain across multiple sessions on the same problem = a trajectory through gold-space.
- **MemRL** near-miss → eventual success is naturally a 2-node edge (`resolved_by`).
- **ERL** heuristics cite the raw experience they came from — `derived_from` edge to the silver/bronze source.
- **Not Always Faithful** says agents follow raw experience over abstract summaries — edges from heuristic gold back to its trajectory source preserve that grounding without forcing the agent to read the whole silver.

Without edges, each gold entry is an isolated heuristic. You lose the ability to reconstruct *how* something was figured out, or to traverse "what else do I know about this file / port / config."

## Three flavors (often conflated)

1. **Wikilinks in body** — `[[entry-slug]]` in markdown. Cheap, human-readable, no schema change. QMD doesn't follow them, but the memory subagent could expand them when synthesizing answers.
2. **Typed relations in frontmatter** — `related_to`, `supersedes`, `depends_on`, `contradicts`, `derived_from`. Structured graph; enables traversal queries.
3. **Auto-derived edges** — reconcile or a separate pass infers links: "these 3 entries reference `scripts/ingest.py`", "this near-miss was resolved by that later entry". Graph emerges from the corpus.

## Why deferred (not in MVP-1)

- **YAGNI under small N.** Graph value compounds with corpus size. Below ~50 entries, edges are noise.
- **Retrieval may not need it.** If QMD + rerank + subagent synthesis already answers cross-project queries well, edges are dead weight.
- **Reconcile gets much harder.** Merging two nodes requires rewriting all incoming edges. Graph mutation is the hard part of every wiki/Zettelkasten system in the wild.
- **Not derived from the research foundations.** None of the 5 papers in `hackathon-brief.md` argue for graph structure. They argue for raw trajectories, concrete-detail heuristics, and LLM-based retrieval. Graph is a *Hippo-original* design idea, not paper-validated. That's fine, but raises the bar for justification.
- **Schema-first risk.** Hand-designing relation types now → schema won't fit the corpus we actually end up with. Better to look at extract+reconcile output first, then ask "what edges am I wishing I had?"

## Where it would slot if/when revisited

**Option A — fold into reconcile.** When the LLM judge in M4/S4.3 classifies a pair as `related`, emit a `related_to` frontmatter edge. Graph emerges as a byproduct. Cheapest entry point.

**Option B — dedicated milestone.** Explicit graph milestone: typed relations + traversal in the memory subagent + visualization. Bigger commitment, probably its own hackathon.

**Option C — wikilinks-only first.** Subagent expands `[[slug]]` references during synthesis. No schema, no traversal queries, no reconcile complications. Lowest-risk way to test "does cross-referencing actually improve answers?"

## Trigger to revisit

After M3 (extract + cross-project query) and M4 (reconcile) ship, look at:

1. Are subagent answers ever incomplete because they pulled one entry but missed an obviously-related one?
2. Does reconcile keep producing `related` pairs that we wish we could record somewhere?
3. Has the corpus grown past ~50–100 entries where flat retrieval starts to feel limiting?

If 2 of 3 → revive this doc, pick an option, scope a hackathon.

## Out of scope here (for whoever picks this up later)

- Visualization (Obsidian-style graph view)
- Backlink computation
- Cycle detection / link integrity
- Edge weighting / typed edge semantics beyond a small fixed vocabulary
