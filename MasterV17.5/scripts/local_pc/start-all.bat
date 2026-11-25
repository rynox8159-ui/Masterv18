@echo off
rem V16.8: the one-click launcher lives in the project root (waits for health checks, then opens the browser).
cd /d %~dp0..\..
call start.bat
