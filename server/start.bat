@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python virtual environment not found: .venv
  echo         Run install.ps1 first, then start this file again.
  pause
  exit /b 1
)

echo Starting DeepSeek chat server...
echo   URL:  http://localhost:8000
echo   Log:  %~dp0data\server.log
echo Press Ctrl+C to stop.
".venv\Scripts\python.exe" run.py %*
