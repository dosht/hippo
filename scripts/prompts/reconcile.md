You are judging a set of gold knowledge-entry clusters produced by a vector-similarity search (QMD). Each cluster contains 2 or more entries that QMD found similar. Your job is to classify every cluster and output a proposal for each one.

## Hard output rules

1. **No preamble.** Do NOT begin with "I'll analyze these clusters…" or any meta-commentary. Your VERY FIRST output line is the first JSONL proposal line.
2. **No closing remarks.** Do NOT end with a summary, note, or reflection. Your LAST output line is the last JSONL proposal line.
3. **Output is a JSONL stream.** One JSON object per line, one line per cluster. No blank lines between them.
4. **No prose outside the JSONL stream.** Everything you output must be a valid JSON line.

## Input format

You will receive a list of clusters. Each cluster block looks like:

```
=== CLUSTER <cluster_id> ===
Members: <entry-id-1>, <entry-id-2>[, ...]
Similarity scores: <score1>, <score2>[, ...]

--- ENTRY: <entry-id-1> ---
<full markdown content of the entry, including YAML frontmatter>

--- ENTRY: <entry-id-2> ---
<full markdown content of the entry, including YAML frontmatter>
```

## Classification rules

For each cluster, classify the relationship between the member entries:

- **`duplicate`**: Two or more entries cover the same specific fact or procedure and one can replace the others without information loss. The oldest or most complete entry is `primary`; the rest go in `others`.
- **`contradiction`**: Entries make conflicting claims about the same thing (e.g., different ports, different API shapes, opposite recommendations). The newer entry supersedes the older. Set `primary` to the newer entry. The `rationale` must name the specific conflict.
- **`related`**: Entries are genuinely related (same domain, same technology, complementary detail) but each carries distinct information that should be kept. Keep both. Graph-edge deference is intentional — the graph-based cross-linking is a separate future step (see docs/brainstorm/graph-gold.md).
- **`unrelated`**: QMD returned a false positive. The entries are not meaningfully connected. No action needed.

## Decision heuristic: inline vs. subagent

You are an orchestrator. For each cluster, decide whether to judge it inline (in this context) or spawn a sub-`claude -p` call for deeper analysis:

- **Judge inline** when: the cluster has 2–3 members, the entries are short (under 400 lines total), and the relationship is clear from the content.
- **Spawn a sub-agent** when: the cluster has 4+ members, OR total entry content exceeds ~500 lines, OR you detect ambiguous contradiction signals (both entries seem authoritative, neither is clearly newer, the conflict is domain-specific). In that case, set `action` to `"subagent-needed"` with a `rationale` explaining what additional analysis is required. The orchestrator layer will re-run those clusters with a dedicated sub-call.

Do NOT use `"subagent-needed"` as a way to avoid difficult judgments. Use it only when the cluster genuinely exceeds what a single-context pass can resolve confidently.

## Token budget guidance

If the total input is large (many clusters with long entries), process all clusters but keep `rationale` concise (1–2 sentences). Rationale is for human review — it does not need to be comprehensive, just enough to explain the decision.

## Output schema

Each line must be a valid JSON object with exactly these fields:

```json
{"cluster_id": "<string>", "action": "duplicate|contradiction|related|unrelated|subagent-needed", "primary": "<entry-id or null>", "others": ["<entry-id>", ...], "rationale": "<1-2 sentence explanation>"}
```

Field rules:
- `cluster_id`: the cluster ID from the input block header.
- `action`: one of the five values above. Lowercase.
- `primary`: the entry to keep or treat as authoritative. `null` for `related` and `unrelated` (no single primary makes sense).
- `others`: for `duplicate`/`contradiction`, the entries that are superseded or merged away. For `related`/`unrelated`, include all member IDs here (since no single primary, list all for completeness). For `subagent-needed`, list all members.
- `rationale`: plain English, no em dashes. 1–2 sentences. For `contradiction`, name the specific conflicting claim.

## Example output

```
{"cluster_id": "c-001", "action": "duplicate", "primary": "mem-lemonsqueezy-webhook-local-testing", "others": ["mem-webhook-local-dev"], "rationale": "Both entries describe the same HMAC-SHA256 signing procedure for LemonSqueezy webhooks. The first is more complete with a full simulator example."}
{"cluster_id": "c-002", "action": "contradiction", "primary": "mem-supabase-cookie-v2", "others": ["mem-supabase-cookie-v1"], "rationale": "v1 recommends httpOnly cookies; v2 (newer) contradicts this by using SameSite=None for cross-origin Supabase requests. Newer entry is authoritative."}
{"cluster_id": "c-003", "action": "related", "primary": null, "others": ["mem-stripe-webhooks", "mem-lemonsqueezy-webhooks"], "rationale": "Both cover webhook patterns but for different payment processors. Each has distinct signing and payload details."}
{"cluster_id": "c-004", "action": "unrelated", "primary": null, "others": ["mem-react-modal-form", "mem-plpgsql-triggers"], "rationale": "QMD false positive. Modal form state and PL/pgSQL triggers share no meaningful relationship."}
```
