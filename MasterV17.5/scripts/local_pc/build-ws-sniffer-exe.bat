@echo off
setlocal
cd /d "%~dp0..\.."

set "PY="
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 --version >nul 2>nul
  if not errorlevel 1 set "PY=py -3.12"
  if not defined PY (
    py -3.11 --version >nul 2>nul
    if not errorlevel 1 set "PY=py -3.11"
  )
  if not defined PY (
    py -3.10 --version >nul 2>nul
    if not errorlevel 1 set "PY=py -3.10"
  )
)
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo [X] Python 3.10-3.12 is required to build the Windows app.
  pause
  exit /b 1
)
%PY% -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)"
if errorlevel 1 (
  echo [X] Python 3.10-3.12 is required to build the Windows app.
  pause
  exit /b 1
)

set "VENV=dependencies\ws-sniffer\.venv"
set "BUNDLE=main_source_code\extensions\ws-sniffer\playwright\dist\ws-sniffer"
set "ZIP=main_source_code\extensions\ws-sniffer\playwright\dist\ws-sniffer-windows.zip"
set "SCRIPT=main_source_code\extensions\ws-sniffer\playwright\playwright_observer.py"

if not exist "%VENV%\Scripts\python.exe" (
  %PY% -m venv "%VENV%" || goto :failed
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip || goto :failed
"%VENV%\Scripts\python.exe" -m pip install -r dependencies\ws-sniffer\requirements-build.txt || goto :failed

"%VENV%\Scripts\pyinstaller.exe" --noconfirm --clean --onedir --console ^
  --name ws-sniffer ^
  --paths main_source_code\backend ^
  --hidden-import api_key_auth ^
  --collect-all playwright ^
  --distpath main_source_code\extensions\ws-sniffer\playwright\dist ^
  --workpath dependencies\ws-sniffer\build ^
  "%SCRIPT%" || goto :failed

set "PLAYWRIGHT_BROWSERS_PATH=%CD%\%BUNDLE%\ms-playwright"
"%VENV%\Scripts\python.exe" -m playwright install chromium || goto :failed
copy /Y main_source_code\extensions\ws-sniffer\playwright\.env.example "%BUNDLE%\.env.example" >nul || goto :failed
copy /Y main_source_code\extensions\ws-sniffer\playwright\README.md "%BUNDLE%\README.md" >nul || goto :failed

powershell -NoProfile -Command "Get-ChildItem -LiteralPath '%BUNDLE%' -Force | Where-Object { $_.Name -ne '.env' } | Compress-Archive -DestinationPath '%ZIP%' -Force" || goto :failed
echo.
echo Build complete:
echo   %CD%\%BUNDLE%\ws-sniffer.exe
echo   %CD%\%ZIP%
echo Copy or extract the entire bundle, then copy .env.example to .env.
pause
exit /b 0

:failed
echo.
echo [X] WS Sniffer build failed. Review the error above.
pause
exit /b 1
