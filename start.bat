@echo off
REM PaperFinder AI - one-click local start for Windows 10/11.
REM Requires Python 3.11+ and (first run only) Node.js 20+ to build the UI.
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (echo Python was not found. Install Python 3.11+ from python.org and tick "Add to PATH". & pause & exit /b 1)

if not exist backend\.venv\Scripts\python.exe (
  echo Creating Python environment...
  python -m venv backend\.venv || (pause & exit /b 1)
  backend\.venv\Scripts\python.exe -m pip install --upgrade pip >nul
  backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt || (pause & exit /b 1)
)

if not exist frontend\dist\index.html (
  where npm >nul 2>nul || (echo Node.js was not found. Install Node.js 20+ from nodejs.org to build the UI. & pause & exit /b 1)
  echo Building the user interface...
  pushd frontend
  call npm ci || (popd & pause & exit /b 1)
  call npm run build || (popd & pause & exit /b 1)
  popd
)

if not exist .env if exist .env.example copy .env.example .env >nul

cd backend
echo Starting PaperFinder AI - close this window or press Ctrl+C to stop.
.venv\Scripts\python.exe -m app
endlocal
