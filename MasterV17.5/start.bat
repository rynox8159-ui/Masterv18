@echo off
setlocal EnableExtensions
title Master Candle V17.2 - start
cd /d %~dp0
set VPY=dependencies\backend\.venv\Scripts\python.exe
set FRONTEND_PORT=5173
for /f "tokens=2 delims==" %%P in ('findstr /b "BACKEND_PORT=" main_source_code\backend\.env') do set BACKEND_PORT=%%P
if not defined BACKEND_PORT set BACKEND_PORT=8001
set BACKEND_PORT=%BACKEND_PORT:"=%

echo ============================================================
echo  Master Candle V17.2 - one click start (MongoDB + backend + frontend + browser)
echo ============================================================
if not exist %VPY% (
  echo [X] Python environment missing. Run setup.bat first ^(one time^).
  pause & exit /b 1
)
if not exist main_source_code\backend\.env (
  echo [X] main_source_code\backend\.env is missing. Copy .env.example to .env and add your Deriv app id / tokens.
  pause & exit /b 1
)

echo [1/6] MongoDB
%VPY% main_source_code\backend\local_doctor.py --mongo-only
if errorlevel 1 (
  echo [X] MongoDB is not running and could not be started.
  echo     Start it: services.msc -^> "MongoDB Server" -^> Start   or install MongoDB Community as a Windows service:
  echo     https://www.mongodb.com/try/download/community
  pause & exit /b 1
)

echo [2/6] Ports
netstat -ano | findstr /r /c:":%BACKEND_PORT% .*LISTENING" >nul && (
  echo [X] Port %BACKEND_PORT% is already in use ^(another backend or program^). Close it or change BACKEND_PORT in main_source_code\backend\.env.
  pause & exit /b 1
)
netstat -ano | findstr /r /c:":%FRONTEND_PORT% .*LISTENING" >nul && (
  echo [X] Port %FRONTEND_PORT% is already in use. Close the other program ^(or an old frontend window^) and run start.bat again.
  pause & exit /b 1
)
echo [OK] ports %BACKEND_PORT% / %FRONTEND_PORT% are free

echo [3/6] Pre-check (packages, .env keys)
%VPY% main_source_code\backend\local_doctor.py --quick
if errorlevel 1 (
  echo [X] Fix the [X] items above, then run start.bat again ^(setup.bat reinstalls packages^).
  pause & exit /b 1
)

echo [4/6] Backend (FastAPI on port %BACKEND_PORT%)
start "Master Candle backend" cmd /k "%~dp0scripts\local_pc\start-backend.bat"
set /a TRIES=0
:waitbackend
set /a TRIES+=1
%VPY% -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:%BACKEND_PORT%/api/health', timeout=2).read(); sys.exit(0)" >nul 2>nul && goto backendok
if %TRIES% GEQ 60 (
  echo [X] Backend did not answer on http://127.0.0.1:%BACKEND_PORT%/api/health after 2 minutes. Read the "Master Candle backend" window for the error.
  pause & exit /b 1
)
timeout /t 2 /nobreak >nul
goto waitbackend
:backendok
echo [OK] backend healthy  http://127.0.0.1:%BACKEND_PORT%/api/health

echo [5/6] Frontend (Vite on port %FRONTEND_PORT%)
start "Master Candle frontend" cmd /k "%~dp0scripts\local_pc\start-frontend.bat"
set /a TRIES=0
:waitfrontend
set /a TRIES+=1
%VPY% -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:%FRONTEND_PORT%/', timeout=2).read(); sys.exit(0)" >nul 2>nul && goto frontendok
if %TRIES% GEQ 45 (
  echo [X] Frontend did not answer on http://localhost:%FRONTEND_PORT% after 90s. Read the "Master Candle frontend" window ^(run setup.bat if packages are missing^).
  pause & exit /b 1
)
timeout /t 2 /nobreak >nul
goto waitfrontend
:frontendok
echo [OK] frontend ready  http://localhost:%FRONTEND_PORT%

echo [6/6] Opening the browser
start "" "http://localhost:%FRONTEND_PORT%/"
echo.
echo All services are running. Keep the backend and frontend windows open; close this window any time.
echo Optional Quotex sniffer: scripts\local_pc\start-chrome-debug.bat + scripts\local_pc\start-sniffer.bat
timeout /t 8 >nul
