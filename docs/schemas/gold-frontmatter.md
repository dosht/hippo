# Gold Entry Frontmatter Schema

Each file in `gold/entries/*.md` has a YAML frontmatter block followed by a markdown body.

## Field Reference

| Field | Owner | Required | Type | Description |
|-------|-------|----------|------|-------------|
| `id` | model | yes | string | Kebab-case slug prefixed `mem-`. Unique across all entries. |
| `type` | model | yes | string | One of: `operational`, `pattern`, `gotcha`, `decision-context`. |
| `topics` | model | yes | list[string] | 3-6 short tags. Used for QMD duplicate detection and filtering. |
| `summary` | model | no | string | One sentence, ≤140 chars. Core insight, not a title restatement. Used by reconcile as the primary QMD query text. Falls back to body-stuffing if absent (backwards compat). |
| `projects` | model | yes | list[string] | `[all]` for universal knowledge; otherwise `[project-name]` to scope to one project. |
| `agents` | model | yes | list[string] | `[all]` or a subset of `[developer, tester, tech-lead, architect]`. |
| `staleness_policy` | model | yes | string | One of: `30d`, `60d`, `90d`, `never`. |
| `source_sessions` | python | yes | list[string] | Session UUIDs that contributed to this entry. Set by `extract.py`. |
| `created` | python | yes | date (YYYY-MM-DD) | Date entry was first written. Set by `extract.py`. |
| `last_validated` | python | yes | date (YYYY-MM-DD) | Date entry was last confirmed accurate. Set/updated by pipeline. |
| `last_queried` | python | yes | date or null | Date entry was last returned by a QMD query. Updated by the memory subagent. |
| `query_count` | python | yes | int | Total times this entry has been returned by a query. |
| `confidence` | python | yes | string | One of: `low`, `medium`, `high`, `verified`. Auto-extracted entries start at `medium`. |
| `supersedes` | python | yes | list[string] | IDs of entries this entry replaces. Empty list if none. |

## Ownership

- **Model-owned fields**: The extract model decides these from silver content. `extract.py` validates they are present (except `summary`, which is optional for backwards compatibility).
- **Python-owned fields**: `extract.py` sets these deterministically. Any value the model emits is overridden.

## Example

```yaml
---
id: mem-chrome-devtools-cli-qa-workflow
type: operational
topics: [chrome-devtools, ai-browser, qa, debugging, forms]
summary: AI Browser exposes CDP on port 9224 (not 9222); use select_page not navigate_page to avoid creating new authenticated tabs.
projects: [all]
agents: [tester, developer]
staleness_policy: 60d
source_sessions: [534c2ba9-cb93-459c-96c0-466e77ac0a95_part_03]
created: 2026-04-26
last_validated: 2026-04-26
last_queried: null
query_count: 0
confidence: medium
supersedes: []
---
```
