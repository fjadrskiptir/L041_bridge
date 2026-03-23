#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Terminal chat only. For Web UI + Telegram + Brave bridge, use Start_Loki_GUI.command (loki_direct_webui.py).

if [ -x "./venv/bin/python" ]; then
  exec ./venv/bin/python loki_direct.py
fi

exec python3 loki_direct.py

