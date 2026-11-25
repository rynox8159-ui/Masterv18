#!/usr/bin/env bash
# Prepares any local MongoDB (collections + indexes) using MONGO_URL / DB_NAME from main_source_code/backend/.env
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/dependencies/backend/.venv/bin/python"; [ -x "$PY" ] || PY=python3
exec "$PY" "$ROOT/main_source_code/database/init_db.py"
