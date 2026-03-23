#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

LABEL="${LOKI_LAUNCHD_LABEL:-com.ness.loki.webui}"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_DIR/$LABEL.plist"
RUNNER="$DIR/run_loki_webui_service.sh"
LOG_OUT="/tmp/loki_launchagent.log"
LOG_ERR="/tmp/loki_launchagent.err.log"

mkdir -p "$LAUNCH_DIR"

if [ ! -f "$RUNNER" ]; then
  echo "[launchd] missing runner script: $RUNNER"
  exit 1
fi

cat >"$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$RUNNER</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$DIR</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$LOG_OUT</string>
  <key>StandardErrorPath</key>
  <string>$LOG_ERR</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo ""
echo "[launchd] installed + started: $LABEL"
echo "[launchd] plist: $PLIST_PATH"
echo "[launchd] logs:"
echo "  tail -f $LOG_OUT"
echo "  tail -f $LOG_ERR"
echo ""
echo "[launchd] You can now use /loki_restart from Telegram and launchd will keep Loki alive."
