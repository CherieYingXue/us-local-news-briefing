@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Checking server...
netstat -ano | findstr ":3847" | findstr "LISTENING" >nul
if errorlevel 1 (
  echo Starting server...
  start "US News Server" /MIN py server.py
  timeout /t 3 /nobreak >nul
)

if not exist cloudflared.exe (
  echo Downloading cloudflared...
  curl.exe -L "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -o cloudflared.exe
)

echo.
echo Starting public tunnel...
echo Your link will appear below in a few seconds.
echo.
cloudflared.exe tunnel --url http://localhost:3847
pause
