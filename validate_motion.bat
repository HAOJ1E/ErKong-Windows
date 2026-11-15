@echo off
setlocal
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0validate_motion.py"
if errorlevel 1 pause
