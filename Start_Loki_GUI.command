#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

URL="${LOKI_WEB_URL:-http://127.0.0.1:7865}"
# Stable path so you can always: tail -f /tmp/loki_direct_webui.log
LOG="/tmp/loki_direct_webui.log"
PIDFILE="/tmp/loki_direct_webui.pid"

PYTHON_BIN="python3"
if [ -x "./venv/bin/python" ]; then
  PYTHON_BIN="./venv/bin/python"
fi

echo "[webui] Launching Loki Direct Web UI..."
echo "[webui] URL: $URL"
echo "[webui] Log: $LOG"

# Extract host/port from URL. Handles both:
# - http://127.0.0.1:7865
# - http://127.0.0.1:7865/some/path
PORT="$(echo "$URL" | sed -E 's/^.*:([0-9]+).*$/\1/')"
if [ -z "$PORT" ]; then PORT="7865"; fi
HOST="$(echo "$URL" | sed -E 's/^.*:\/+([^:/]+)(:.*)?$/\1/')"
if [ -z "$HOST" ]; then HOST="127.0.0.1"; fi

while true; do
  if command -v lsof >/dev/null 2>&1; then
    PIDS="$(lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -z "$PIDS" ]; then
      break
    fi
  fi
  PORT="$((PORT+1))"
done

URL="http://$HOST:$PORT"
if command -v lsof >/dev/null 2>&1; then
  EXISTING_PIDS="$(lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$EXISTING_PIDS" ]; then
    echo "[webui] Stopping existing server(s) on port $PORT: $EXISTING_PIDS"
    kill $EXISTING_PIDS >/dev/null 2>&1 || true
    sleep 1
  fi
fi

# Fallback: stop any lingering instances by name.
if command -v pkill >/dev/null 2>&1; then
  pkill -f "loki_direct_webui.py" >/dev/null 2>&1 || true
  sleep 0.5
fi

# Stop an existing server if we have a PID file.
if [ -f "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" >/dev/null 2>&1; then
    echo "[webui] Stopping existing server pid=$OLD_PID"
    kill "$OLD_PID" >/dev/null 2>&1 || true
    sleep 1
  fi
fi

export LOKI_WEB_HOST="$HOST"
export LOKI_WEB_PORT="$PORT"
export PYTHONUNBUFFERED=1

# Fresh log for this run (Flask + [webui]/[tts] lines appear here in real time).
{
  echo "===== Loki Web UI log $(date) ====="
  echo "[webui] Using python: $PYTHON_BIN"
  echo "[webui] Listen: $URL"
} >"$LOG"

# nohup + stdin detached: keeps server alive when this Terminal tab closes.
# -u / PYTHONUNBUFFERED: log lines appear immediately (not stuck in buffer).
nohup env PYTHONUNBUFFERED=1 "$PYTHON_BIN" -u loki_direct_webui.py >>"$LOG" 2>&1 </dev/null &
PID=$!
echo "$PID" > "$PIDFILE"

echo "[webui] PID: $PID (saved in $PIDFILE)"
echo "[webui] Log file: $LOG"
echo "[webui] Telegram: look for lines starting with [telegram] in the log below (or open $URL/api/telegram/status)"

# Wait until the server is reachable.
for i in $(seq 1 20); do
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
      break
    fi
  else
    # If curl is missing, just sleep and rely on next open attempt.
    sleep 1
  fi
  sleep 0.5
done

if command -v open >/dev/null 2>&1; then
  open "$URL" || true
fi

echo ""
echo "[webui] Server is running in the background."
echo "[webui] This window will STREAM THE LOG below (you should see each click as HTTP lines)."
echo "[webui] Press Ctrl+C to stop watching — the server keeps running until you: kill $PID"
echo ""
tail -f "$LOG"
