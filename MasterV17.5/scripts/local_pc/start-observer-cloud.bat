@echo off
rem Quotex observer on this PC -> cloud backend https://event-stream-hub-7.preview.emergentagent.com (uses your home IP, no proxy needed).
title Master Candle - Quotex observer (cloud)
cd /d %~dp0..\..
if not exist dependencies\backend\.venv\Scripts\python.exe (echo Run setup-windows.bat first & pause & exit /b 1)
set OBSERVER_BACKEND_URL=https://event-stream-hub-7.preview.emergentagent.com
dependencies\backend\.venv\Scripts\python.exe -m playwright install chromium
cd main_source_code\extensions\ws-sniffer\playwright
..\..\..\..\dependencies\backend\.venv\Scripts\python.exe playwright_observer.py
pause
