@echo off
title NAQSO frontend (http://localhost:5173)
cd /d %~dp0..\..
call npm --prefix dependencies\frontend run dev
pause
