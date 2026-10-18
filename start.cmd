@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing. Run setup.ps1 first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\serve.py
echo.
echo Server stopped. Any startup error appears above.
pause
