@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

echo ===============================================================================
echo   Z-PACT Enterprise - Automated Android APK Builder
echo ===============================================================================
echo.

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

"%PYTHON_EXE%" "android_app\build_apk_engine.py"
echo.
pause
