#!/bin/bash
set -euo pipefail

LABEL="${LOKI_LAUNCHD_LABEL:-com.ness.loki.webui}"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true

if [ -f "$PLIST_PATH" ]; then
  rm -f "$PLIST_PATH"
  echo "[launchd] removed: $PLIST_PATH"
else
  echo "[launchd] plist not found: $PLIST_PATH"
fi

echo "[launchd] stopped + uninstalled: $LABEL"
