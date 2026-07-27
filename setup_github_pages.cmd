@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  echo Git is not available. Install Git for Windows first.
  pause
  exit /b 1
)

set /p "REMOTE_URL=GitHub repository URL (example: https://github.com/user/fund-holdings-app.git): "
if "%REMOTE_URL%"=="" (
  echo Repository URL is required.
  pause
  exit /b 1
)

if not exist ".git" git init
git branch -M main
git remote remove origin >nul 2>nul
git remote add origin "%REMOTE_URL%"
git add index.html latest-quotes.json generate_latest_quotes.py server.py .github\workflows\update-latest-quotes.yml .gitignore
git commit -m "Publish fund holdings app" >nul 2>nul
git push -u origin main

echo.
echo Done. In GitHub, enable Pages from Settings - Pages - Deploy from branch - main / root.
echo After that, the smartphone URL is usually:
echo https://USER.github.io/REPOSITORY/
pause
