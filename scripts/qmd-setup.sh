#!/usr/bin/env bash
# qmd-setup.sh -- Register QMD collections and generate embeddings for Hippo.
#
# Run this once after cloning, or after installing qmd for the first time.
# On first run, qmd embed downloads local GGUF models (~329MB embedding model
# plus reranking/query-expansion models, totaling roughly 2GB). This is a
# one-time download; subsequent runs are fast.
#
# Usage:
#   ./scripts/qmd-setup.sh
#
# Prerequisites:
#   npm install -g @tobilu/qmd

set -euo pipefail

HIPPO_ROOT="${HIPPO_HOME:-$HOME/src/hippo}"

# Gold dir defaults to <repo>/gold/entries; override with HIPPO_GOLD_DIR
# (e.g. point at a private repo at ~/Documents/hippo-data/gold/entries).
HIPPO_GOLD_DIR_RESOLVED="${HIPPO_GOLD_DIR:-$HIPPO_ROOT/gold/entries}"

mkdir -p "$HIPPO_GOLD_DIR_RESOLVED"

echo "==> Registering hippo collection ($HIPPO_GOLD_DIR_RESOLVED)..."
qmd collection add "$HIPPO_GOLD_DIR_RESOLVED" --name hippo --mask "**/*.md"

echo ""
echo "==> Registering hippo-silver collection (silver/)..."
echo "    Note: silver/ may not exist yet. This collection is used in MVP-1-08.5."
echo "    If the command fails, create the silver/ directory first:"
echo "      mkdir -p \"$HIPPO_ROOT/silver\""

if [ -d "$HIPPO_ROOT/silver" ]; then
    qmd collection add "$HIPPO_ROOT/silver" --name hippo-silver --mask "**/*.md"
else
    echo "    silver/ not found, skipping hippo-silver registration."
    echo "    Run the following once silver/ exists:"
    echo "      qmd collection add \"$HIPPO_ROOT/silver\" --name hippo-silver --mask \"**/*.md\""
fi

echo ""
echo "==> Generating embeddings (downloads models on first run, ~2GB total)..."
qmd embed

echo ""
echo "==> Done. Verify with: qmd status"
