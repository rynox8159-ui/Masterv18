"""Local PC pre-flight check: python packages, .env, MongoDB (auto-start on Windows), Deriv reachability, observer browser."""
import importlib
import os
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
QUICK = '--quick' in sys.argv
MONGO_ONLY = '--mongo-only' in sys.argv
problems = 0


def report(ok, label, hint=''):
    global problems
    problems += 0 if ok else 1
    print(f"  [{'OK' if ok else 'X '}] {label}" + ('' if ok or not hint else f'\n       -> {hint}'))


def mongo_reachable(url, timeout_ms=2500):
    from pymongo import MongoClient
    MongoClient(url, serverSelectionTimeoutMS=timeout_ms).admin.command('ping')


def try_start_windows_mongo():
    """Start the MongoDB Windows service (needs an elevated prompt when the service is stopped)."""
    if platform.system() != 'Windows':
        return False
    for service in ('MongoDB', 'MongoDB Server', 'mongodb'):
        try:
            result = subprocess.run(['net', 'start', service], capture_output=True, text=True, timeout=40)
        except (OSError, subprocess.TimeoutExpired):
            continue
        output = f'{result.stdout}\n{result.stderr}'.lower()
        if result.returncode == 0 or 'already been started' in output:
            return True
        if 'access is denied' in output:
            print('       -> MongoDB service is stopped and needs admin rights: right-click start-all.bat -> "Run as administrator",')
            print('          or start it from services.msc -> MongoDB Server -> Start')
            return False
    return False


print('Master Candle V17.2 local check')
env_file = HERE / '.env'
from dotenv import load_dotenv  # noqa: E402
load_dotenv(env_file)
mongo = os.environ.get('MONGO_URL', 'mongodb://127.0.0.1:27017')

if MONGO_ONLY:
    try:
        mongo_reachable(mongo)
        report(True, f'MongoDB reachable ({mongo})')
    except Exception:  # noqa: BLE001
        started = try_start_windows_mongo()
        time.sleep(3 if started else 0)
        try:
            mongo_reachable(mongo)
            report(True, f'MongoDB started and reachable ({mongo})')
        except Exception:  # noqa: BLE001
            report(False, f'MongoDB reachable ({mongo})', 'install MongoDB Community as a Windows service: https://www.mongodb.com/try/download/community')
    sys.exit(1 if problems else 0)

report(sys.version_info >= (3, 10), f'Python {sys.version.split()[0]}', 'Install Python 3.11 (torch has no wheel for 3.13+)')
if sys.version_info >= (3, 13):
    report(False, 'Python version supported by torch', 'Use Python 3.11 or 3.12 for the virtual environment (setup-windows.bat picks it automatically)')
for module in ('fastapi', 'uvicorn', 'motor', 'numpy', 'pandas', 'torch', 'sklearn', 'xgboost', 'lightgbm', 'catboost', 'websockets', 'playwright', 'joblib'):
    try:
        importlib.import_module(module)
        report(True, f'package {module}')
    except OSError as exc:
        hint = 'torch DLL load failed: install Microsoft Visual C++ Redistributable x64 (https://aka.ms/vs/17/release/vc_redist.x64.exe), then rerun setup-windows.bat' if module == 'torch' else f'rerun the local setup script ({type(exc).__name__})'
        report(False, f'package {module}', hint)
    except Exception as exc:  # noqa: BLE001
        report(False, f'package {module}', f'rerun the local setup script ({type(exc).__name__})')

report(env_file.is_file(), 'main_source_code/backend/.env present', 'copy main_source_code/backend/.env.example to main_source_code/backend/.env')
for key in ('MONGO_URL', 'DB_NAME'):
    report(bool(os.environ.get(key)), f'backend .env {key} set', f'add {key}= to main_source_code/backend/.env')
for key in ('DERIV_APP_ID', 'DERIV_API_TOKEN_1'):
    report(bool(os.environ.get(key)), f'backend .env {key} set (auto trade)', f'add {key}= to main_source_code/backend/.env')

try:
    mongo_reachable(mongo)
    report(True, f'MongoDB reachable ({mongo})')
except Exception:  # noqa: BLE001
    print('  [..] MongoDB not reachable - trying to start the Windows service ...')
    started = try_start_windows_mongo()
    if started:
        time.sleep(3)
    try:
        mongo_reachable(mongo)
        report(True, f'MongoDB started and reachable ({mongo})')
    except Exception:  # noqa: BLE001
        report(False, f'MongoDB reachable ({mongo})', 'start MongoDB (services.msc -> MongoDB Server -> Start) or install MongoDB Community as a service')

port = int(os.environ.get('BACKEND_PORT', '7007'))
if QUICK:
    with socket.socket() as sock:
        busy = sock.connect_ex(('127.0.0.1', port)) == 0
    report(not busy, f'port {port} free for backend', 'another backend is already running - close that window first')

if not QUICK:
    try:
        socket.create_connection(('api.derivws.com', 443), timeout=4).close()
        report(True, 'Deriv public API reachable')
    except OSError:
        report(False, 'Deriv public API reachable', 'check internet / firewall')
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            report(Path(pw.chromium.executable_path).exists(), 'Chromium for Quotex observer', 'run the dependencies/backend virtualenv Python with -m playwright install chromium')
    except Exception as exc:  # noqa: BLE001
        report(False, 'Chromium for Quotex observer', f'playwright install chromium ({type(exc).__name__})')

print('All checks passed.' if not problems else f'{problems} problem(s) found.')
sys.exit(1 if problems else 0)
