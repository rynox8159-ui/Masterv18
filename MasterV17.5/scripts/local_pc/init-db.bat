@echo off
REM Prepares any local MongoDB (collections + indexes) using MONGO_URL / DB_NAME from main_source_code\backend\.env
cd /d %~dp0..\..
set VPY=dependencies\backend\.venv\Scripts\python.exe
if not exist %VPY% set VPY=python
%VPY% main_source_code\database\init_db.py
pause
