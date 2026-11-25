#!/usr/bin/env bash
# Attaches the WS sniffer to YOUR Chrome tab (opened by start-chrome-debug.sh on port 9222) and forwards
# the Quotex pair list, live ticks and 1m candles of ALL open pairs to the backend. Read-only: no trades are placed.
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/dependencies/backend/.venv/bin/python"
[ -x "$PY" ] || { echo "Run scripts/local_pc/setup-linux.sh first"; exit 1; }
export OBSERVER_MODE=ws_sniffer
export PLAYWRIGHT_CDP_URL="${PLAYWRIGHT_CDP_URL:-http://127.0.0.1:9222}"
echo "Attaching to Chrome at $PLAYWRIGHT_CDP_URL ... (if nothing is found, run start-chrome-debug.sh first)"
cd "$ROOT/main_source_code/extensions/ws-sniffer/playwright"
exec "$PY" playwright_observer.py
