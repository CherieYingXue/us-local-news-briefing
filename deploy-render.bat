@echo off
chcp 65001 >nul
cd /d "%~dp0"

set GH=%TEMP%\gh\bin\gh.exe
if not exist "%GH%" (
  echo Downloading GitHub CLI...
  curl.exe -L "https://github.com/cli/cli/releases/download/v2.74.2/gh_2.74.2_windows_amd64.zip" -o "%TEMP%\gh.zip"
  powershell -Command "Expand-Archive -Path '%TEMP%\gh.zip' -DestinationPath '%TEMP%\gh' -Force"
)

echo ============================================
echo  Push to GitHub + Deploy on Render
echo ============================================
echo.

echo [1/2] Pushing latest code to GitHub...
py push_via_api.py
if errorlevel 1 (
  echo Push failed. Try: git push origin master
  pause
  exit /b 1
)

echo.
echo [2/2] Trigger Render deploy...
echo   In Render Dashboard:
echo   1. Open your service: us-local-news-briefing
echo   2. Click "Manual Deploy" ^> "Deploy latest commit"
echo   3. Wait 3-5 minutes until status shows "Live"
echo.
start https://dashboard.render.com/
echo.
echo Live app: https://us-local-news-briefing.onrender.com
pause
