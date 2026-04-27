#!/usr/bin/env bash
# scripts/status.sh -- Minimal pipeline status summary.
#
# Prints three pieces of information:
#   1. Session counts grouped by manifest status (bronze / silver / gold /
#      skipped-large / failed) using jq.
#   2. Gold entry file count (find gold/entries/ -name "*.md" | wc -l).
#   3. QMD collection health (qmd status).
#
# This script is a read-only convenience wrapper. It does NOT run any pipeline
# stage. All three commands are idempotent.
#
# Usage (from project root):
#   bash scripts/status.sh
#
# Wave 3b / Story S12.
# Do NOT add staleness, promotion, or suggestions logic here -- those are P3.
#
# Expected output format:
#
#   === Hippo Pipeline Status ===
#
#   -- Session counts by status --
#   [ {"status": "bronze", "count": N}, ... ]
#
#   -- Gold entry count --
#   N entries in gold/entries/
#
#   -- QMD health --
#   <output of qmd status>

set -euo pipefail

MANIFEST="${MANIFEST:-manifest.jsonl}"
GOLD_DIR="${GOLD_DIR:-gold/entries}"

echo "=== Hippo Pipeline Status ==="
echo ""

echo "-- Session counts by status --"
cat "$MANIFEST" | jq -s 'group_by(.status) | map({status: .[0].status, count: length})'

echo ""
echo "-- Gold entry count --"
count=$(find "$GOLD_DIR" -maxdepth 1 -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
echo "$count entries in gold/entries/"

echo ""
echo "-- QMD health --"
qmd status
