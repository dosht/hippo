#!/usr/bin/env bash
# scripts/nightly/run.sh -- Hippo nightly pipeline (first attempt).
#
# Runs ingest -> compact -> extract -> reconcile (cluster + judge) -> qmd refresh.
# Apply step is NOT included — proposals sit for morning review.
#
# Resumable: each stage processes only entries in the right input status, so a
# re-run after a quota wall continues from where it stopped.
#
# Invoked by launchd at 21:30 daily (com.mu.hippo.nightly.plist).

set -euo pipefail

HIPPO_HOME="${HIPPO_HOME:-$HOME/src/hippo-public}"
LOG="${HIPPO_NIGHTLY_LOG:-$HOME/.claude/hippo-nightly.log}"

cd "$HIPPO_HOME"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }

mkdir -p "$(dirname "$LOG")"
log "==== nightly run start (pid $$) ===="
log "HIPPO_HOME=$HIPPO_HOME"

run_stage() {
  local name="$1"; shift
  log ">> $name"
  if "$@" >> "$LOG" 2>&1; then
    log "<< $name OK"
  else
    local rc=$?
    log "<< $name FAILED rc=$rc — continuing to next stage (pipeline is resumable)"
    return 0
  fi
}

run_stage "ingest"             python -m scripts.ingest
run_stage "compact"            python -m scripts.compact
run_stage "extract"            python -m scripts.extract
run_stage "reconcile cluster"  python -m scripts.reconcile cluster --full
run_stage "reconcile judge"    python -m scripts.reconcile judge
run_stage "qmd update"         qmd update --collection hippo
run_stage "qmd embed"          qmd embed --collection hippo

log "manifest status:"
jq -r '.status' manifest.jsonl 2>/dev/null | sort | uniq -c | sed 's/^/    /' | tee -a "$LOG"

log "==== nightly run done ===="
