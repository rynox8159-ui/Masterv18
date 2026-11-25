#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" && exec dependencies/backend/.venv/bin/python main_source_code/backend/server.py
