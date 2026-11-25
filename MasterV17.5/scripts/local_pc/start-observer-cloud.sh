#!/usr/bin/env bash
# Quotex observer on this PC -> cloud backend https://event-stream-hub-7.preview.emergentagent.com (uses your home IP, no proxy needed).
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/dependencies/backend/.venv/bin/python"
[ -x "$PY" ] || { echo "Run scripts/local_pc/setup-linux.sh first"; exit 1; }
"$PY" -m playwright install chromium
cd "$ROOT/main_source_code/extensions/ws-sniffer/playwright"
OBSERVER_BACKEND_URL=https://event-stream-hub-7.preview.emergentagent.com exec "$PY" playwright_observer.py
