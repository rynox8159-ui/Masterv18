#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
python3 -m venv dependencies/backend/.venv
dependencies/backend/.venv/bin/pip install --upgrade pip
dependencies/backend/.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
dependencies/backend/.venv/bin/pip install -r dependencies/backend/requirements.txt
dependencies/backend/.venv/bin/python -m playwright install chromium chromium-headless-shell
[ -f main_source_code/backend/.env ] || cp main_source_code/backend/.env.example main_source_code/backend/.env
[ -f main_source_code/frontend/.env ] || cp main_source_code/frontend/.env.example main_source_code/frontend/.env
npm --prefix dependencies/frontend install
[ -e main_source_code/frontend/node_modules ] || ln -s ../../dependencies/frontend/node_modules main_source_code/frontend/node_modules
dependencies/backend/.venv/bin/python main_source_code/database/init_db.py || true
dependencies/backend/.venv/bin/python main_source_code/backend/local_doctor.py || true
echo "Setup finished. Start MongoDB, then ./scripts/local_pc/start-all.sh"
