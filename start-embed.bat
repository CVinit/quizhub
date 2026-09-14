@echo off
REM QuizHub one-click launcher (logic lives in start-embed.ps1)
REM Options: start-embed.bat -Port 9000  |  start-embed.bat -Rebuild
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-embed.ps1" %*
if errorlevel 1 pause
