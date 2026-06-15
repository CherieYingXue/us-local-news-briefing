@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  Deploy to Render.com (Permanent Hosting)
echo ============================================
echo.

where gh >nul 2>&1
if errorlevel 1 (
  echo Step 1: Install GitHub CLI from https://cli.github.com/
  echo         Then run: gh auth login
  pause
  exit /b 1
)

gh auth status >nul 2>&1
if errorlevel 1 (
  echo Please log in to GitHub first:
  gh auth login
)

echo.
echo [1/3] Creating GitHub repository...
gh repo create us-local-news-briefing --public --source=. --remote=origin --push
if errorlevel 1 (
  echo Repo may already exist. Trying push only...
  git remote add origin https://github.com/%USERNAME%/us-local-news-briefing.git 2>nul
  git push -u origin master
)

echo.
echo [2/3] GitHub push complete!
echo.
echo [3/3] Now deploy on Render:
echo   1. Open https://dashboard.render.com/blueprints
echo   2. Click "New Blueprint Instance"
echo   3. Connect GitHub and select: us-local-news-briefing
echo   4. Click "Apply" - your permanent URL will be:
echo      https://us-local-news-briefing.onrender.com
echo.
start https://dashboard.render.com/blueprints
pause
