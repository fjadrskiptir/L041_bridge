#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Xcode / Apple *Developer* Pythons can import tkinter but abort in TkpInit (Tcl_Panic → SIGABRT).
# Reject those by real sys.executable path before we use Tk for a real window.
python_ok_for_overlay() {
  local c="$1"
  if [ -z "$c" ] || [ ! -x "$c" ]; then
    return 1
  fi
  "$c" -c "
import sys
exe = sys.executable
bad = (
    'Xcode.app' in exe
    or '/Developer/Library/Frameworks/Python3.framework/' in exe
    or ('CommandLineTools' in exe and 'Python3.framework' in exe)
)
if bad:
    raise SystemExit(2)
import tkinter
" 2>/dev/null || return 1
  return 0
}

pick_python_with_tk() {
  local c
  # Prefer likely-good installs before generic PATH (which may be Xcode’s python3).
  for c in \
    "/usr/bin/python3" \
    "/opt/homebrew/bin/python3" \
    "/usr/local/bin/python3" \
    "$DIR/venv/bin/python" \
    "$(command -v python3 || true)"; do
    if python_ok_for_overlay "$c"; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

err_no_python() {
  echo "[overlay] ERROR: No usable Python with desktop Tk found."
  echo "[overlay] Xcode / Command Line Tools Python often crashes the overlay (TkpInit → Tcl_Panic)."
  echo "[overlay] Fix one of:"
  echo "  1) brew install python@3.13 python-tk@3.13"
  echo "     Then re-run this script (or recreate venv with that python)."
  echo "  2) Install python.org macOS build and point LOKI_OVERLAY_PYTHON=/path/to/python3"
}

if [ -n "${LOKI_OVERLAY_PYTHON:-}" ]; then
  if ! python_ok_for_overlay "$LOKI_OVERLAY_PYTHON"; then
    echo "[overlay] ERROR: LOKI_OVERLAY_PYTHON is missing Tk or is Xcode/CLT Python (will crash)."
    err_no_python
    exit 1
  fi
  PY="$LOKI_OVERLAY_PYTHON"
else
  PY="$(pick_python_with_tk)" || {
    err_no_python
    exit 1
  }
fi

echo "[overlay] Starting L041 presence overlay..."
echo "[overlay] Python: $PY"
echo "[overlay] Presence URL: ${LOKI_OVERLAY_PRESENCE_URL:-http://127.0.0.1:7865/api/presence}"

exec "$PY" "$DIR/loki_presence_overlay.py"
