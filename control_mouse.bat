@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON=%PROJECT_DIR%.venv\Scripts\pythonw.exe"
if not exist "%PYTHON%" (
  echo ErKong virtual environment not found.
  echo Run the project setup steps first.
  pause
  exit /b 1
)
start "" "%PYTHON%" "%PROJECT_DIR%control_mouse.py"
endlocal
