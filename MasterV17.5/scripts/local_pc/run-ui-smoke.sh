#!/usr/bin/env bash
# Playwright UI smoke test against the running app (default http://localhost:5173)
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/dependencies/backend/.venv/bin/python"; [ -x "$PY" ] || PY=python3
"$PY" -m playwright install chromium >/dev/null 2>&1
exec "$PY" "$ROOT/tests/ui_smoke.py" "${1:-http://localhost:5173}"
