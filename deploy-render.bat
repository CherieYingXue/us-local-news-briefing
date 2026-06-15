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
echo  Deploy US Local News to Render (Permanent)
echo ============================================
echo.

"%GH%" auth status >nul 2>&1
if errorlevel 1 (
  echo STEP A: Log in to GitHub
  echo   A browser window will open. Enter the code shown below.
  echo.
  "%GH%" auth login --hostname github.com --git-protocol https --web
  if errorlevel 1 exit /b 1
)

echo.
echo STEP B: Push code to GitHub...
"%GH%" repo create us-local-news-briefing --public --source=. --remote=origin --push 2>nul
if errorlevel 1 (
  for /f "delims=" %%i in ('"%GH%" api user -q .login') do set GHUSER=%%i
  git remote remove origin 2>nul
  git remote add origin https://github.com/%GHUSER%/us-local-news-briefing.git
  git branch -M main 2>nul
  git push -u origin master 2>nul || git push -u origin main
)

for /f "delims=" %%i in ('"%GH%" api user -q .login') do set GHUSER=%%i
echo.
echo GitHub repo: https://github.com/%GHUSER%/us-local-news-briefing
echo.

echo STEP C: Deploy on Render...
echo   1. Browser opens Render Blueprint page
echo   2. Connect GitHub if asked
echo   3. Select repo: us-local-news-briefing
echo   4. Click "Apply"
echo.
echo Your permanent URL will be:
echo   https://us-local-news-briefing.onrender.com
echo.
start https://dashboard.render.com/blueprints
echo Done! Complete the Render steps in your browser.
pause
