@echo off
setlocal
cd /d "%~dp0"
set "PY_CMD="
py -3.11 --version >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3.11"
if not defined PY_CMD (
  python --version >nul 2>&1
  if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
  echo Python 3.11 or newer is required. Install it from https://www.python.org/downloads/windows/
  echo During setup, enable the option to add Python to PATH.
  exit /b 1
)
%PY_CMD% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo Python 3.11 or newer is required. The selected Python version is too old.
  exit /b 1
)
echo Creating virtual environment...
%PY_CMD% -m venv .venv
if errorlevel 1 goto :error
echo Installing application dependencies...
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :error
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :error
echo Initializing SQLite and local demo data...
.venv\Scripts\python.exe scripts\init_db.py
if errorlevel 1 goto :error
echo.
echo Setup complete. Run run.bat to start PayPilot AI.
exit /b 0
:error
echo Setup failed. Review the error above, verify Python 3.11+ and internet access, then run setup.bat again.
exit /b 1
