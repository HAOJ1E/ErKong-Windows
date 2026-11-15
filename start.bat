@echo off
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Python virtual environment not found.
    echo Run py -3.12 -m venv .venv from the project folder.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0main.py"
if errorlevel 1 pause