@echo off
rem Opens YOUR Google Chrome (or Edge) with remote debugging on port 9222 and its own profile folder.
rem Log in to Quotex manually in this window, then run start-sniffer.bat (or /sniffer -> "Start WS sniffer").
title Master Candle V17.2 - Chrome (remote debugging 9222)
cd /d %~dp0..\..
set PORT=9222
set PROFILE=%LOCALAPPDATA%\MasterCandle\qx-chrome-profile
if not exist "%PROFILE%" mkdir "%PROFILE%"
set CHROME=
for %%P in ("%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%LocalAppData%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe") do if not defined CHROME if exist %%P set CHROME=%%P
if not defined CHROME (echo [X] Google Chrome / Microsoft Edge not found. Install Chrome: https://www.google.com/chrome & pause & exit /b 1)
echo [OK] browser: %CHROME%
start "" %CHROME% --remote-debugging-port=%PORT% --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check https://market-qx.info/en/trade
echo.
echo Chrome is open with remote debugging on port %PORT% (profile: %PROFILE%).
echo  1) Log in to Quotex in that Chrome window (manual login, captcha / 2FA is fine - the login is remembered).
echo  2) Then run scripts\local_pc\start-sniffer.bat  or press "Start WS sniffer" on the /sniffer page.
echo     The sniffer only reads the WebSocket feed of that tab - it never places trades on Quotex.
timeout /t 10 >nul
