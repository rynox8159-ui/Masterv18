@echo off
rem Runs the Quotex Playwright observer on this PC (your home IP is not blocked by Quotex).
rem Uses the backend venv from setup-windows.bat; backend URL = BACKEND_PORT in main_source_code\backend\.env
rem or OBSERVER_BACKEND_URL in main_source_code\extensions\ws-sniffer\playwright\.env.
title Master Candle - Quotex observer
cd /d %~dp0..\..
if not exist dependencies\backend\.venv\Scripts\python.exe (echo Run setup-windows.bat first & pause & exit /b 1)
dependencies\backend\.venv\Scripts\python.exe -m playwright install chromium
cd main_source_code\extensions\ws-sniffer\playwright
..\..\..\..\dependencies\backend\.venv\Scripts\python.exe playwright_observer.py
pause
