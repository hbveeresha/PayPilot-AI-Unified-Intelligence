@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run setup.bat first.
  exit /b 1
)
set "PYTHONPATH=%CD%\backend"
echo Starting PayPilot AI. Open http://127.0.0.1:8000 in your browser.
echo Press Ctrl+C to stop the local server.
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
