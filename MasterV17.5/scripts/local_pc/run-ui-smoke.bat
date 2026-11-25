@echo off
REM Playwright UI smoke test against the running app (default http://localhost:5173)
cd /d %~dp0..\..
set VPY=dependencies\backend\.venv\Scripts\python.exe
if not exist %VPY% set VPY=python
%VPY% -m playwright install chromium >nul 2>nul
%VPY% tests\ui_smoke.py %1
pause
