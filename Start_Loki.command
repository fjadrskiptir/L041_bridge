#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ -x "./venv/bin/python" ]; then
  exec ./venv/bin/python loki_direct.py
fi

exec python3 loki_direct.py

