@echo off
cd /d "%~dp0"
echo === Build verification.json (report section 3 data) ===
echo.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "tools\build_verification_data.py"
) else (
  python "tools\build_verification_data.py"
)
echo.
echo === Done. Press any key to close ===
pause >nul
