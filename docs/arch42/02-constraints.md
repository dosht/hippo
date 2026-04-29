# Section 2: Architecture Constraints

## Technical Constraints

| Constraint | Source | Impact |
|------------|--------|--------|
| Runs on a single macOS laptop | User environment | No cloud compute for capture; launchd for scheduling |
| Claude Code is the agent host | Design decision | Hooks (`SessionStart`, `UserPromptSubmit`) are the only injection points |
| `~/.claude/projects/` is the only session source | Claude Code internals | Ingest must run locally; cannot run in a cloud Routine |
| Claude Code deletes sessions after ~30 days | Claude Code GC policy | Bronze copy must happen before GC; nightly cadence required |
| Max subscription (no API key billing) | User setup | Pipeline uses `claude -p` calls; subject to daily rate limits |
| No custom MCP server or CLI binary | ADR-001 | All pipeline logic lives in Python scripts and Claude Code skills |
| Python 3.11+ only | Script runtime | No external dependencies beyond stdlib + `pathlib` for core scripts |

## Organisational Constraints

| Constraint | Source | Impact |
|------------|--------|--------|
| Single-user system | Scope | No multi-tenancy, no auth layer needed |
| Personal data stays local | Privacy | Bronze, traces, index all in `~/.hippo/`; never synced to cloud storage |
| Git-tracked data must be non-personal | Public repo (`hippo-public`) | Only framework code and sample entries are tracked; real traces gitignored |
| No automatic curated-doc modification | Team convention | Hippo never writes to `docs/`, `arc42/`, `developer-guide/` or stories |

## Scope Exclusions

- No model fine-tuning. All learning via context injection.
- No multi-harness ingestion yet (Hermes, Codex). Claude Code only.
- No cross-machine real-time sync. Git pull + index rebuild is sufficient.
- No general-purpose knowledge base. Operational knowledge from sessions only.
