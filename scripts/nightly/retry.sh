#!/usr/bin/env bash
# scripts/nightly/retry.sh -- Quota-wall recovery for the nightly pipeline.
#
# Flips `failed` manifest entries back to their prior input status:
#   - failed without silver_path  -> bronze (compact will retry)
#   - failed with silver_path     -> silver (extract will retry)
# Then re-invokes scripts/nightly/run.sh to push them through again.
#
# Invoked by launchd at 23:30 daily (com.mu.hippo.retry.plist), ~2hr after
# the first attempt — gives Anthropic's per-day quota a chance to refresh.

set -euo pipefail

HIPPO_HOME="${HIPPO_HOME:-$HOME/src/hippo}"
LOG="${HIPPO_NIGHTLY_LOG:-$HOME/.claude/hippo-nightly.log}"
MANIFEST="$HIPPO_HOME/manifest.jsonl"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }

mkdir -p "$(dirname "$LOG")"
log "==== nightly retry start (pid $$) ===="

if [[ ! -f "$MANIFEST" ]]; then
  log "no manifest at $MANIFEST — nothing to retry"
  exit 0
fi

failed_count=$(jq '[.[] | select(.status == "failed")] | length' --slurp "$MANIFEST")
log "failed entries before flip: $failed_count"

if [[ "$failed_count" -eq 0 ]]; then
  log "nothing failed — skipping retry"
  exit 0
fi

# Flip failed -> prior input status. Heuristic: if silver_path is set, the
# entry got past compact; the failure was in extract -> retry from silver.
# Otherwise the failure was in compact -> retry from bronze.
tmp="$(mktemp)"
jq -c '
  if .status == "failed" then
    if .silver_path then
      .status = "silver"
    else
      .status = "bronze"
    end
    | .error = null
  else . end
' "$MANIFEST" > "$tmp"
mv "$tmp" "$MANIFEST"

log "flipped $failed_count failed entries — re-running pipeline"
exec "$HIPPO_HOME/scripts/nightly/run.sh"
