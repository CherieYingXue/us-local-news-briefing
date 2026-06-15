@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing Python dependencies...
py -m pip install -r requirements.txt -q
echo.
echo Starting US Local News Briefing server...
echo.
py server.py
pause
