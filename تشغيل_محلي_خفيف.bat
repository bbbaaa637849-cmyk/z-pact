@echo off
chcp 65001 >nul
cd /d "%~dp0"
set ZPACT_LOW_CPU_MODE=1
set OMP_NUM_THREADS=1
set OPENBLAS_NUM_THREADS=1
set MKL_NUM_THREADS=1

echo ========================================================
echo   Z-PACT Low-CPU Local Launcher
echo   Sequential Batch Stamping + Light Worker Mode
echo ========================================================
echo.
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment .venv not found!
    pause
    exit /b 1
)

".venv\Scripts\python.exe" orchestrator.py %*
pause

