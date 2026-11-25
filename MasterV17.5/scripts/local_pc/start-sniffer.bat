@echo off
rem Attaches the WS sniffer to YOUR Chrome tab (opened by start-chrome-debug.bat on port 9222) and forwards
rem the Quotex pair list, live ticks and 1m candles of ALL open pairs to the backend. Read-only: no trades are placed.
title Master Candle V17.2 - WS sniffer (your Chrome tab)
cd /d %~dp0..\..
if not exist dependencies\backend\.venv\Scripts\python.exe (echo Run setup-windows.bat first & pause & exit /b 1)
set OBSERVER_MODE=ws_sniffer
if not defined PLAYWRIGHT_CDP_URL set PLAYWRIGHT_CDP_URL=http://127.0.0.1:9222
echo Attaching to Chrome at %PLAYWRIGHT_CDP_URL% ... (if nothing is found, run start-chrome-debug.bat first)
cd main_source_code\extensions\ws-sniffer\playwright
..\..\..\..\dependencies\backend\.venv\Scripts\python.exe playwright_observer.py
pause
