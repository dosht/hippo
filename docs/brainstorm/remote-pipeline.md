# Remote-Run Pipeline (Post-MVP)

**Status:** Brainstorm / deferred. Captured 2026-04-26 during MVP-1 hackathon (M4 reconcile design).

**Tl;dr:** MVP-1 runs the whole pipeline (compact → extract → reconcile) on the user's laptop because QMD's index is local-only. Three paths exist for moving this to a remote scheduler; pick after MVP-1 ships.

## Why this is deferred

QMD (`@tobilu/qmd`) stores its index under `~/.qmd/`, not in the repo. QMD 2.1.0 lacks a flag to relocate the index. Reconcile depends on QMD for candidate-neighbor lookups (`M4/S4.2`). Therefore reconcile must run wherever the QMD index lives — today, the laptop.

## Three paths

### Path A — Everything local (MVP-1 choice)

- Routine = Claude Desktop scheduled task on the laptop.
- Pipeline: laptop has bronze, silver, gold, QMD index, all scripts.
- **Pro:** simplest; no sync problems; reconcile uses QMD natively.
- **Con:** laptop must be awake when the routine fires; doesn't scale to multi-machine.
- **Why MVP-1:** the brief already lists Cloud Routine as out-of-scope. Path A requires zero new infra.

### Path B — Remote pipeline, rebuild QMD index per run

- Cloud Routine pulls repo, runs compact + extract, runs `qmd update && qmd embed` on the remote, then reconcile uses that fresh remote index, then commits gold + pushes.
- **Pro:** laptop-independent.
- **Con:** rebuilds index every run (wasteful — embedding API calls cost real money; ~$0.05 per 500-entry corpus, scales linearly). Index never lives anywhere durable; every run is cold. Also: QMD's local-SQLite assumption may not survive in an ephemeral cloud filesystem without extra work.

### Path C — Hybrid: cloud does compact+extract, local does reconcile

- Cloud Routine handles the expensive Claude calls (compact, extract). Pushes new gold to git.
- Laptop has a local hook (post-pull or local Routine) that runs `qmd update && qmd embed && python -m scripts.reconcile` when it sees new gold, then opens a PR or commits proposals.
- **Pro:** best of both — Claude calls run unattended in cloud; QMD-stateful reconcile stays where the index lives.
- **Con:** split-brain orchestration; two trigger points; reconcile lags extract until the laptop next syncs.
- **Likely post-MVP-1 choice.**

## Trigger to revisit

After MVP-1 ships and the local pipeline has run a few times. Decide between:

- Path A is fine forever (laptop is always around).
- Path C if the user starts wanting unattended runs (vacation, multi-machine, shared team gold).

Path B is a fallback only if QMD ships a remote-index option or we replace QMD with something cloud-native.

## Implementation notes for whichever path is picked

- The `compact → extract → reconcile` skill should be path-agnostic at the orchestration layer. Each script is a single-purpose CLI; the skill just runs them in sequence.
- If we ever do Path C, the `M4/reconcile:` git diff convention (used to identify "new since last reconcile") still works — laptop reconciles whatever extract pushed since the last local reconcile commit.
