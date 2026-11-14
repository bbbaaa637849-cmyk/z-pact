@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

echo ===============================================================================
echo   Starting Z-PACT Unified Ecosystem (Core + Bot + SMS Webhook)...
echo ===============================================================================

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

"%PYTHON_EXE%" orchestrator.py
pause
