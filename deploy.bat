@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  US Local News - Public Deploy
echo ========================================
echo.

netstat -ano | findstr ":3847" | findstr "LISTENING" >nul
if errorlevel 1 (
  echo [1/2] Starting server...
  start "US News Server" /MIN py server.py
  timeout /t 3 /nobreak >nul
) else (
  echo [1/2] Server already running on port 3847
)

echo [2/2] Starting public tunnel...
echo.
echo Your public link will appear below in ~10 seconds.
echo Keep this window open. Closing it stops public access.
echo.
ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=60 -R 80:127.0.0.1:3847 nokey@localhost.run
pause
