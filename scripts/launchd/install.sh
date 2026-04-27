#!/usr/bin/env bash
# scripts/launchd/install.sh -- Install Hippo nightly launchd jobs.
#
# Copies plists to ~/Library/LaunchAgents/ and bootstraps them.
# Idempotent: re-running unloads the old job before reloading.
#
# To uninstall: scripts/launchd/uninstall.sh

set -euo pipefail

HIPPO_HOME="${HIPPO_HOME:-$HOME/src/hippo}"
LA_DIR="$HOME/Library/LaunchAgents"
SRC="$HIPPO_HOME/scripts/launchd"

mkdir -p "$LA_DIR"

for label in com.mu.hippo.nightly com.mu.hippo.retry; do
  plist="$LA_DIR/$label.plist"
  echo "==> $label"

  # Unload existing if present (ignore "not loaded" errors)
  launchctl bootout "gui/$UID/$label" 2>/dev/null || true

  # Copy plist
  cp "$SRC/$label.plist" "$plist"
  chmod 644 "$plist"

  # Load
  launchctl bootstrap "gui/$UID" "$plist"
  echo "    loaded — next run scheduled per StartCalendarInterval"
done

echo
echo "Installed. Verify with:"
echo "  launchctl list | grep hippo"
echo
echo "Logs:"
echo "  ~/.claude/hippo-nightly.log     (combined)"
echo "  ~/.claude/hippo-nightly.stdout.log / .stderr.log"
echo "  ~/.claude/hippo-retry.stdout.log  / .stderr.log"
echo
echo "Manual smoke test (don't wait until 21:30):"
echo "  launchctl kickstart -k gui/$UID/com.mu.hippo.nightly"
