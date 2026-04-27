#!/bin/bash
# Install Hippo git hooks into .git/hooks/.
# Run once on every machine after cloning or re-cloning the repo.
# Safe to re-run: overwrites existing hooks.

set -e

HIPPO_HOME="${HIPPO_HOME:-$(dirname "$(cd "$(dirname "$0")" && pwd)")}"
HOOKS_SRC="$HIPPO_HOME/scripts"
HOOKS_DST="$HIPPO_HOME/.git/hooks"

if [ ! -d "$HOOKS_DST" ]; then
    echo "ERROR: $HOOKS_DST not found. Is this a git repo?"
    exit 1
fi

install_hook() {
    local name="$1"
    local src="$HOOKS_SRC/$name"
    local dst="$HOOKS_DST/$name"
    if [ ! -f "$src" ]; then
        echo "WARNING: $src not found, skipping."
        return
    fi
    cp "$src" "$dst"
    chmod +x "$dst"
    echo "Installed: $dst"
}

install_hook post-merge

echo "Done. Git hooks installed for repo at $HIPPO_HOME."
