@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo ErKong virtual environment not found.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements-blink.txt
if errorlevel 1 exit /b 1
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" download_blink_model.py
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 exit /b 1
echo Blink components ready. Camera stays off until enabled in ErKong.
pause
endlocal
