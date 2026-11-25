#!/usr/bin/env bash
# Runs the Quotex Playwright observer on this PC (your home IP is not blocked by Quotex).
# Uses the backend venv from setup-linux.sh; backend URL = BACKEND_PORT in main_source_code/backend/.env
# or OBSERVER_BACKEND_URL in main_source_code/extensions/ws-sniffer/playwright/.env.
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/dependencies/backend/.venv/bin/python"
[ -x "$PY" ] || { echo "Run scripts/local_pc/setup-linux.sh first"; exit 1; }
"$PY" -m playwright install chromium
cd "$ROOT/main_source_code/extensions/ws-sniffer/playwright"
exec "$PY" playwright_observer.py
