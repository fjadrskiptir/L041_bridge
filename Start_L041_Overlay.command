#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

pick_python_with_tk() {
  local c
  for c in "/usr/bin/python3" "$DIR/venv/bin/python" "$(command -v python3 || true)"; do
    if [ -z "$c" ] || [ ! -x "$c" ]; then
      continue
    fi
    if "$c" -c "import tkinter" 2>/dev/null; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(pick_python_with_tk)" || {
  echo "[overlay] ERROR: No Python with Tk found."
  echo "[overlay] Homebrew Python often has no _tkinter. Fix one of:"
  echo "  1) Use Apple Python: /usr/bin/python3 (usually has Tk)"
  echo "  2) Or: brew install python-tk@3.13   (then re-run this script)"
  exit 1
}

echo "[overlay] Starting L041 presence overlay..."
echo "[overlay] Python: $PYTHON_BIN"
echo "[overlay] Presence URL: ${LOKI_OVERLAY_PRESENCE_URL:-http://127.0.0.1:7865/api/presence}"

exec "$PYTHON_BIN" "$DIR/loki_presence_overlay.py"
