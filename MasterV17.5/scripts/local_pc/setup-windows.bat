@echo off
setlocal EnableExtensions
title Master Candle V17.2 - one-time setup
cd /d %~dp0..\..
echo ============================================================
echo  Master Candle V17.2 setup (Python backend + React frontend)
echo ============================================================
rem Python 3.11 or 3.12 is required: torch CPU wheels are not published for 3.13+/3.9-.
set PY=
for %%V in (3.11 3.12) do (
  if not defined PY ( py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V" )
)
if not defined PY ( python -c "import sys; raise SystemExit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>nul && set "PY=python" )
if not defined PY (
  echo [X] Python 3.11 or 3.12 not found. Install Python 3.11 from https://www.python.org/downloads/release/python-3119/ ^(tick "Add to PATH"^)
  pause & exit /b 1
)
%PY% --version
where node >nul 2>nul || (echo [X] Node.js 20+ not found. Install from https://nodejs.org & pause & exit /b 1)
for /f "tokens=*" %%N in ('node --version') do echo [OK] Node.js %%N

echo [1/7] Python virtual environment dependencies\backend\.venv
if not exist dependencies\backend\.venv\Scripts\python.exe %PY% -m venv dependencies\backend\.venv || (echo [X] could not create the virtual environment & pause & exit /b 1)
set VPY=dependencies\backend\.venv\Scripts\python.exe
%VPY% -m pip install --upgrade pip wheel setuptools >nul

echo [2/7] PyTorch (CPU wheel) - fixes the "[X] package torch (OSError)" check
%VPY% -c "import torch" >nul 2>nul && (echo [OK] torch already installed) || (
  %VPY% -m pip uninstall -y torch >nul 2>nul
  %VPY% -m pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu || (echo [X] torch install failed - check internet / disk space & pause & exit /b 1)
  %VPY% -c "import torch; print('[OK] torch', torch.__version__)" || (
    echo [X] torch imports with OSError. Install the "Microsoft Visual C++ Redistributable x64" from
    echo     https://aka.ms/vs/17/release/vc_redist.x64.exe  then run this script again.
    pause & exit /b 1
  )
)

echo [3/7] Backend packages (ML / DL / DS libraries) - first run takes a few minutes
%VPY% -m pip install -r dependencies\backend\requirements.txt || (echo [X] pip install failed & pause & exit /b 1)

echo [4/7] Chromium for the Quotex observer (Playwright)
%VPY% -m playwright install chromium chromium-headless-shell || echo [!] Chromium download failed - rerun later: %VPY% -m playwright install chromium

echo [5/7] Environment files
if not exist main_source_code\backend\.env copy main_source_code\backend\.env.example main_source_code\backend\.env >nul
if not exist main_source_code\frontend\.env.local echo VITE_API_SAME_ORIGIN=true> main_source_code\frontend\.env.local
echo [OK] main_source_code\backend\.env

echo [6/7] MongoDB service
%VPY% main_source_code\backend\local_doctor.py --mongo-only
%VPY% main_source_code\database\init_db.py

echo [7/7] Frontend packages
call npm --prefix dependencies\frontend install || (echo [X] npm install failed & pause & exit /b 1)
if not exist main_source_code\frontend\node_modules mklink /J "%CD%\main_source_code\frontend\node_modules" "%CD%\dependencies\frontend\node_modules" >nul

echo.
echo ---------------- Final check ----------------
%VPY% main_source_code\backend\local_doctor.py
echo.
echo Setup finished. Run scripts\local_pc\start-all.bat
pause
