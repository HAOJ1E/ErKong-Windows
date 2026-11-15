@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON=%PROJECT_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo NodLink virtual environment not found.
  echo Set up the Python environment before building.
  pause
  exit /b 1
)
"%PYTHON%" -m PyInstaller --noconfirm --clean --windowed --collect-all mediapipe --add-data "%PROJECT_DIR%models;models" --name NodLinkWindows --paths "%PROJECT_DIR%" "%PROJECT_DIR%main.py"
if errorlevel 1 (
  echo EXE build failed.
  pause
  exit /b 1
)
echo Build complete: %PROJECT_DIR%dist\NodLinkWindows\NodLinkWindows.exe
echo WPods service and USB/IP driver remain separate prerequisites.
pause
endlocal
