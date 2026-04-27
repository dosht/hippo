#!/usr/bin/env bash
# scripts/run-pipeline.sh -- Sequential M4 pipeline runner.
#
# Runs ingest -> compact -> extract -> reconcile (cluster, judge, apply).
# Stops on first failure. Each stage is resumable: re-run after a quota
# wall by flipping `failed` entries back to the prior status, e.g.:
#   jq -c 'if .status=="failed" then .status="silver" else . end' \
#     manifest.jsonl > /tmp/m && mv /tmp/m manifest.jsonl
#
# Usage:
#   scripts/run-pipeline.sh [--source PATH]
#     --source PATH   passed through to scripts.ingest (optional)

set -euo pipefail

SOURCE_ARGS=()
APPROVE_FLAG="--approve-all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE_ARGS+=("--source" "$2"); shift 2 ;;
    --interactive) APPROVE_FLAG=""; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

echo "==> 1/5 ingest"
python -m scripts.ingest "${SOURCE_ARGS[@]}"

echo "==> 2/5 compact"
python -m scripts.compact

echo "==> 3/5 extract"
python -m scripts.extract

echo "==> 4/5 reconcile cluster"
python -m scripts.reconcile cluster

echo "==> 5/5 reconcile judge"
python -m scripts.reconcile judge

if [[ -n "$APPROVE_FLAG" ]]; then
  echo "==> reconcile apply ($APPROVE_FLAG)"
  python -m scripts.reconcile apply "$APPROVE_FLAG"
else
  echo "==> reconcile-proposals.jsonl ready. Review, then run:"
  echo "    python -m scripts.reconcile apply --approve <c001,c003,...>"
fi

echo "==> done."
