#!/usr/bin/env bash
# scripts/launchd/uninstall.sh -- Remove Hippo nightly launchd jobs.

set -euo pipefail

LA_DIR="$HOME/Library/LaunchAgents"

for label in com.mu.hippo.nightly com.mu.hippo.retry; do
  plist="$LA_DIR/$label.plist"
  echo "==> $label"
  launchctl bootout "gui/$UID/$label" 2>/dev/null || echo "    not loaded"
  if [[ -f "$plist" ]]; then
    rm "$plist"
    echo "    removed $plist"
  fi
done

echo "Uninstalled."
