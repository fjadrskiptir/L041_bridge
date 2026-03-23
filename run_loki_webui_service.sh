#!/bin/bash
set -euo pipefail

# Service runner for launchd (non-interactive). Keep this separate from
# Start_Loki_GUI.command, which is optimized for interactive local launching.

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PYTHON_BIN="python3"
if [ -x "./venv/bin/python" ]; then
  PYTHON_BIN="./venv/bin/python"
fi

export PYTHONUNBUFFERED=1
export LOKI_WEB_HOST="${LOKI_WEB_HOST:-127.0.0.1}"
export LOKI_WEB_PORT="${LOKI_WEB_PORT:-7865}"

echo "[launchd] starting loki_direct_webui.py host=$LOKI_WEB_HOST port=$LOKI_WEB_PORT python=$PYTHON_BIN"
exec "$PYTHON_BIN" -u "$DIR/loki_direct_webui.py"
