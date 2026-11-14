@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

echo ===============================================================================
echo   Z-PACT Unified Ecosystem and Telegram Bot
echo ===============================================================================

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

"%PYTHON_EXE%" orchestrator.py
pause
