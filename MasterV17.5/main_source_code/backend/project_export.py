"""Full project backup ZIP (MasterV17.3.zip): all code, hidden files, .env files/secrets, requirements and a
bounded MongoDB snapshot. V16.8: built in the background into _export/, verified with zipfile.testzip() + SHA-256 and
served as a static file (a streamed on-the-fly ZIP was cut by the 60 s proxy timeout -> "archive is corrupt")."""
import asyncio
import hashlib
import json
import os
import time
import zipfile
from pathlib import Path

from bson import json_util
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Full ready-to-deploy backup: every file incl. hidden files, .env files and secrets.
SKIP_DIRS = {
    'node_modules', '__pycache__', '.git', '.venv', 'venv', '.venv-local', 'dist',
    'build', 'site-packages', '.vite', '.pytest_cache', '.qx-profile', '.qx-chrome-profile', '.cache', 'logs',
    'dump', '.emergent', 'test_reports', '.ruff_cache', '_export',
}
SKIP_SUFFIXES = {'.pyc', '.pyo', '.log'}
SKIP_FILES = {'main_source_code/frontend/public/Masterv12-verified.zip', 'main_source_code/frontend/public/MasterV16.3.zip', 'main_source_code/frontend/public/MasterV16.8.zip', 'main_source_code/frontend/public/MasterV16.9.zip', 'main_source_code/frontend/public/MasterV17.3.zip', 'MasterV17.3.zip', 'LOCAL_RUN.txt', 'ZIP_MANIFEST.json'}
DB_DOC_LIMIT = {'market_ticks': 20000, 'market_candles': 60000, 'runtime_events': 5000, 'market_features': 40000}
DEFAULT_DB_DOC_LIMIT = 10000
ROOT_NAME = 'MasterV17.3'
REQUIREMENTS = 'dependencies/backend/requirements.txt'

# Every path below must exist in the ZIP; the manifest check reports anything missing.
REQUIRED_FILES = (
    'README.md', 'requirements.txt',
    'main_source_code/backend/.env', 'main_source_code/backend/.env.example', 'main_source_code/backend/requirements.txt',
    'main_source_code/backend/server.py', 'main_source_code/backend/deriv_service.py', 'main_source_code/backend/signal_service.py',
    'main_source_code/backend/training_orchestrator.py', 'main_source_code/backend/feature_store.py', 'main_source_code/backend/project_export.py',
    'main_source_code/backend/local_doctor.py', 'main_source_code/backend/autotrade_routes.py', 'main_source_code/backend/observation_routes.py',
    'main_source_code/backend/ml/ml_model_adapters.py', 'main_source_code/backend/ml/market_ml_orchestrator.py',
    'main_source_code/backend/dl/deep_model_service.py',
    'main_source_code/backend/ds/feature_engineering.py', 'main_source_code/backend/ds/ensemble_fusion.py',
    'main_source_code/backend/ds/forecasting/time_series_predictor.py', 'main_source_code/backend/ds/forecasting/signal_confluence.py',
    'main_source_code/backend/ds/forecasting/risk_manager.py', 'main_source_code/backend/ds/forecasting/market_filter.py',
    'main_source_code/backend/ds/forecasting/timeframe_analyzer.py',
    'main_source_code/frontend/.env.local', 'main_source_code/frontend/index.html', 'main_source_code/frontend/vite.config.mts',
    'main_source_code/frontend/src/main.tsx', 'main_source_code/frontend/src/App.tsx', 'main_source_code/frontend/src/pages/Home.tsx',
    'main_source_code/frontend/src/pages/AutoTrade.tsx', 'main_source_code/frontend/src/components/terminal/TrainingPanel.tsx',
    'main_source_code/frontend/src/components/terminal/SnifferProof.tsx', 'main_source_code/frontend/src/components/terminal/ConnectionStrip.tsx',
    'main_source_code/extensions/ws-sniffer/playwright/playwright_observer.py', 'main_source_code/extensions/ws-sniffer/playwright/README.md',
    'main_source_code/database/restore_mongo.py',
    'main_source_code/backend/algorithm_detector.py', 'main_source_code/backend/market_agent_registry.py',
    'main_source_code/frontend/src/components/terminal/v163.css', 'main_source_code/frontend/src/components/terminal/BootSplash.tsx',
    'scripts/local_pc/start-backend.sh', 'scripts/local_pc/start-frontend.sh',
    'dependencies/backend/requirements.txt', 'dependencies/frontend/package.json',
    'auto_trade/dashboard_engine.py', 'auto_trade/dashboard_execution.py', 'main_source_code/backend/ml/model_validation.py',
    'scripts/local_pc/setup-windows.bat', 'scripts/local_pc/start-all.bat', 'scripts/local_pc/start-backend.bat',
    'scripts/local_pc/start-frontend.bat', 'scripts/local_pc/start-observer.bat', 'scripts/local_pc/start-observer-cloud.bat',
    'scripts/local_pc/setup-linux.sh', 'scripts/local_pc/start-all.sh', 'scripts/local_pc/build-ws-sniffer-exe.bat',
    'scripts/local_pc/start-chrome-debug.bat', 'scripts/local_pc/start-chrome-debug.sh', 'scripts/local_pc/start-sniffer.bat', 'scripts/local_pc/start-sniffer.sh',
    'docs_and_blueprints/README.md', 'docs_and_blueprints/PRD.md', 'docs_and_blueprints/PROJECT_ARCHITECTURE.md', 'docs_and_blueprints/CHANGELOG_V16.3.md', 'docs_and_blueprints/CHANGELOG_V16.8.md', 'docs_and_blueprints/CHANGELOG_V17.1.md', 'docs_and_blueprints/CHANGELOG_V17.3.md',
)
REQUIRED_ENV_KEYS = ('MONGO_URL', 'DB_NAME', 'DERIV_APP_ID', 'DERIV_API_TOKEN_1', 'DERIV_API_TOKEN_2', 'BACKEND_PORT', 'AUTOTRADE_WEB_LIVE_ENABLED', 'AUTOTRADE_WEB_REAL_CONTROL_KEY')


def manifest_check(names):
    """Compare the expected file list (and .env secret keys) against what the archive contains."""
    present = set(names)
    missing = [name for name in REQUIRED_FILES if name not in present]
    env_text = (PROJECT_ROOT / 'main_source_code/backend/.env').read_text() if (PROJECT_ROOT / 'main_source_code/backend/.env').is_file() else ''
    missing_env = [key for key in REQUIRED_ENV_KEYS if f'{key}=' not in env_text]
    model_files = sorted(name for name in present if name.startswith('main_source_code/backend/models/'))
    return {
        'ok': not missing and not missing_env, 'expectedFiles': len(REQUIRED_FILES), 'presentExpected': len(REQUIRED_FILES) - len(missing),
        'missingFiles': missing, 'missingEnvKeys': missing_env, 'modelFiles': model_files,
        'envFiles': sorted(name for name in present if Path(name).name.startswith('.env')), 'totalFiles': len(present),
    }

LOCAL_FRONTEND_ENV = """VITE_API_SAME_ORIGIN=true
"""

LOCAL_RUN = """MASTER V17.3 - LOCAL PC RUN (Windows / Linux / macOS)
====================================================
Needs: Python 3.11 or 3.12, Node.js 20+, MongoDB 6+ running on 127.0.0.1:27017.
This ZIP already contains main_source_code/backend/.env with your keys (keep it private).

Windows:     setup.bat   (one time)  ->  start.bat   (every time; waits for MongoDB + backend + frontend health, then opens the browser)
Linux/macOS: ./scripts/local_pc/setup-linux.sh     (one time)  ->  ./scripts/local_pc/start-all.sh   (every time)

Backend : http://127.0.0.1:8001  (BACKEND_PORT in main_source_code/backend/.env; the frontend proxy follows it)
Frontend: http://localhost:5173
Old data (optional): python main_source_code/database/restore_mongo.py
Training is automatic (TRAIN_ALL_ON_STARTUP=true, then every 24h); Training tab -> Train All runs it on demand.
Auto trade: /autotrade -> tabs Live P&L / Open Trades / History / Risk Settings -> Start DEMO first (real = operator key + typed REAL).
Accuracy proof: /accuracy -> per pair win rate, samples, break-even, last training; PROMOTED only at >=58% over >=300 OOS trades.
Quotex (your own Chrome tab, manual login): 1) scripts/local_pc/start-chrome-debug.(bat|sh)  2) log in to Quotex in that Chrome
                                             3) scripts/local_pc/start-sniffer.(bat|sh)  (or /sniffer -> Start WS sniffer in the UI)
Quotex in a separate Chromium window instead: scripts/local_pc/start-observer.(bat|sh)
Quotex blocked on the cloud server? Run scripts/local_pc/start-observer-cloud.(bat|sh) on your home PC.
Prepare any local MongoDB (collections + indexes): python main_source_code/database/init_db.py   (setup scripts run it)
UI smoke test (Playwright): scripts/local_pc/run-ui-smoke.(sh|bat)  [http://localhost:5173]
Auto trade V17.3: /autotrade -> DEMO | REAL | DEMO + REAL -> ALL SIGNALS or HIGH CONFIDENCE ONLY -> stake (flat or martingale) -> limits -> START.
Every Event-stream signal is bought on Deriv at its entry candle (same pair, direction, duration). Brain picks: /api/v1/brain/overview
Training V17.3: 2 markets in parallel, full retrain every 6h, incremental retrain every 30 min from settled signals, Training tab -> Retrain Active.
Check everything: python main_source_code/backend/local_doctor.py
"""


def cloud_observer_scripts(origin):
    bat = f"""@echo off
rem Quotex observer on this PC -> cloud backend {origin} (uses your home IP, no proxy needed).
title Master Candle - Quotex observer (cloud)
cd /d %~dp0..\\..
if not exist dependencies\\backend\\.venv\\Scripts\\python.exe (echo Run setup-windows.bat first & pause & exit /b 1)
set OBSERVER_BACKEND_URL={origin}
dependencies\\backend\\.venv\\Scripts\\python.exe -m playwright install chromium
cd main_source_code\\extensions\\ws-sniffer\\playwright
..\\..\\..\\..\\dependencies\\backend\\.venv\\Scripts\\python.exe playwright_observer.py
pause
"""
    sh = f"""#!/usr/bin/env bash
# Quotex observer on this PC -> cloud backend {origin} (uses your home IP, no proxy needed).
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/dependencies/backend/.venv/bin/python"
[ -x "$PY" ] || {{ echo "Run scripts/local_pc/setup-linux.sh first"; exit 1; }}
"$PY" -m playwright install chromium
cd "$ROOT/main_source_code/extensions/ws-sniffer/playwright"
OBSERVER_BACKEND_URL={origin} exec "$PY" playwright_observer.py
"""
    return {'scripts/local_pc/start-observer-cloud.bat': bat.replace('\n', '\r\n'), 'scripts/local_pc/start-observer-cloud.sh': sh}


def public_origin(request):
    host = request.headers.get('x-forwarded-host') or request.headers.get('host') or ''
    proto = request.headers.get('x-forwarded-proto') or request.url.scheme
    if not host or host.split(':')[0] in {'127.0.0.1', 'localhost'}:
        return None
    return f'{proto.split(",")[0].strip()}://{host.split(",")[0].strip()}'



class DownloadBody(BaseModel):
    password: str | None = Field(default=None, max_length=256)  # ignored; kept for old clients


def _iter_files():
    for current, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not (Path(current) / name).is_symlink()]
        for name in files:
            path = Path(current) / name
            if (
                path.is_file()
                and not path.is_symlink()
                and path.relative_to(PROJECT_ROOT).as_posix() not in SKIP_FILES
                and path.suffix not in SKIP_SUFFIXES
                and path.suffix != '.zip'
            ):
                yield path


async def _dump_database(db):
    manifest, files = {}, {}
    for name in sorted(await db.list_collection_names()):
        limit = DB_DOC_LIMIT.get(name, DEFAULT_DB_DOC_LIMIT)
        cursor = db[name].find({}).sort('_id', -1).limit(limit)
        lines = [json_util.dumps(doc) async for doc in cursor]
        manifest[name] = {'documents': len(lines), 'limit': limit}
        files[f'main_source_code/database/dump/{name}.jsonl'] = '\n'.join(lines)
    files['main_source_code/database/dump/_manifest.json'] = json.dumps({'exportedAt': time.time(), 'collections': manifest}, indent=2)
    return manifest, files


EXPORT_DIR = PROJECT_ROOT / '_export'
ZIP_PATH = EXPORT_DIR / f'{ROOT_NAME}.zip'


def _write_zip(target, extras, db_files, db_manifest):
    """Blocking: runs in a worker thread. Writes to a temp file, verifies, then renames atomically."""
    EXPORT_DIR.mkdir(exist_ok=True)
    tmp = target.with_suffix('.zip.tmp')
    names = set()
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in _iter_files():
            name = path.relative_to(PROJECT_ROOT).as_posix()
            archive.write(path, f'{ROOT_NAME}/{name}')
            names.add(name)
        for name, text in extras.items():
            info = zipfile.ZipInfo(f'{ROOT_NAME}/{name}', time.localtime()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if name.endswith('.sh') else 0o644) << 16
            archive.writestr(info, text)
        names.update(extras)
        for name, text in db_files.items():
            archive.writestr(f'{ROOT_NAME}/{name}', text)
        check = manifest_check(names)
        archive.writestr(
            f'{ROOT_NAME}/ZIP_MANIFEST.json',
            json.dumps({
                'name': ROOT_NAME, 'files': len(names), 'collections': db_manifest,
                'createdAt': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
                'runtimeSecretsIncluded': True, 'envFiles': check['envFiles'], 'manifestCheck': check, 'fileList': sorted(names),
            }, indent=2),
        )
    with zipfile.ZipFile(tmp) as verify:
        bad = verify.testzip()
        if bad:
            raise RuntimeError(f'ZIP verification failed at {bad}')
        entries = len(verify.namelist())
    sha = hashlib.sha256()
    with open(tmp, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            sha.update(chunk)
    os.replace(tmp, target)
    target.with_suffix('.zip.sha256').write_text(f'{sha.hexdigest()}  {target.name}\n')
    return {'files': len(names), 'entries': entries, 'sha256': sha.hexdigest(), 'size': target.stat().st_size, 'manifest': check}


class ZipBuilder:
    def __init__(self, db):
        self.db = db
        self.task = None
        self.state = {'state': 'idle', 'startedAt': None, 'finishedAt': None, 'error': None, 'result': None}
        if ZIP_PATH.is_file():
            sha_file = ZIP_PATH.with_suffix('.zip.sha256')
            self.state.update(state='ready', finishedAt=ZIP_PATH.stat().st_mtime, result={
                'size': ZIP_PATH.stat().st_size, 'sha256': sha_file.read_text().split()[0] if sha_file.is_file() else None})

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def start(self, origin):
        if not self.running:
            self.state.update(state='building', startedAt=time.time(), finishedAt=None, error=None)
            self.task = asyncio.create_task(self._build(origin))
        return self.status()

    async def _build(self, origin):
        self.state.update(state='building', startedAt=time.time(), finishedAt=None, error=None)
        try:
            extras = {'LOCAL_RUN.txt': LOCAL_RUN}
            if origin:
                extras.update(cloud_observer_scripts(origin))
            if not (PROJECT_ROOT / 'main_source_code/frontend/.env.local').is_file():
                extras['main_source_code/frontend/.env.local'] = LOCAL_FRONTEND_ENV
            db_manifest, db_files = await _dump_database(self.db)
            result = await asyncio.to_thread(_write_zip, ZIP_PATH, extras, db_files, db_manifest)
            self.state.update(state='ready', finishedAt=time.time(), result=result)
        except Exception as exc:  # noqa: BLE001
            self.state.update(state='error', finishedAt=time.time(), error=f'{type(exc).__name__}: {exc}')

    def status(self):
        fresh = ZIP_PATH.is_file()
        return {**self.state, 'name': f'{ROOT_NAME}.zip', 'fileReady': fresh and self.state['state'] == 'ready',
                'size': ZIP_PATH.stat().st_size if fresh else None, 'ageSeconds': round(time.time() - ZIP_PATH.stat().st_mtime) if fresh else None}


def project_router(db):
    router = APIRouter(prefix='/api/v1/project', tags=['project-export'])
    builder = ZipBuilder(db)

    @router.post('/verify')
    async def verify(body: DownloadBody | None = None):
        return {'ok': True, 'passwordRequired': False}

    @router.post('/build')
    async def build(request: Request):
        return builder.start(public_origin(request))

    @router.get('/status')
    async def status():
        return builder.status()

    @router.get('/download')
    async def download_file():
        if builder.running or not ZIP_PATH.is_file():
            raise HTTPException(409, 'ZIP_NOT_READY')
        result = builder.state.get('result') or {}
        check = result.get('manifest') or {}
        return FileResponse(
            ZIP_PATH, media_type='application/zip', filename=f'{ROOT_NAME}.zip',
            headers={
                'Cache-Control': 'no-store', 'X-Manifest-Ok': 'false' if check.get('ok') is False else 'true',
                'X-Manifest-Missing': str(len(check.get('missingFiles', [])) + len(check.get('missingEnvKeys', []))),
                'X-Zip-Sha256': result.get('sha256') or '',
                'Access-Control-Expose-Headers': 'Content-Disposition, X-Manifest-Ok, X-Manifest-Missing, X-Zip-Sha256, Content-Length',
            },
        )

    @router.post('/download')
    async def download(request: Request, body: DownloadBody | None = None):
        # Old clients: build (if needed) and wait up to 50 s, then serve the verified file.
        builder.start(public_origin(request))
        deadline = time.monotonic() + 50
        while builder.running and time.monotonic() < deadline:
            await asyncio.sleep(1)
        if builder.running:
            raise HTTPException(202, 'ZIP_BUILDING')
        if builder.state['state'] == 'error':
            raise HTTPException(500, builder.state['error'])
        return await download_file()

    @router.get('/manifest')
    async def manifest():
        names = {path.relative_to(PROJECT_ROOT).as_posix() for path in _iter_files()}
        names.update({'LOCAL_RUN.txt', 'main_source_code/frontend/.env.local', 'requirements.txt', 'main_source_code/backend/requirements.txt'})
        return {'name': ROOT_NAME, **manifest_check(names)}

    return router
