@echo off
title Master Candle backend (port from backend .env)
cd /d %~dp0..\..
dependencies\backend\.venv\Scripts\python.exe main_source_code\backend\server.py
pause
