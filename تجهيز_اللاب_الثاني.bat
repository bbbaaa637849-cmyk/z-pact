@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================================
echo   Z-PACT Ecosystem — Laptop 2 Setup Wizard
echo ================================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating Python virtual environment .venv ...
    where uv >nul 2>&1
    if not errorlevel 1 (
        uv venv .venv --seed
    ) else (
        set "BASE_PY="
        python -c "import sys" >nul 2>&1
        if not errorlevel 1 (
            set "BASE_PY=python"
        ) else (
            py -3 -c "import sys" >nul 2>&1
            if not errorlevel 1 (
                set "BASE_PY=py -3"
            ) else if exist "%LOCALAPPDATA%\Python\bin\python.exe" (
                set "BASE_PY=%LOCALAPPDATA%\Python\bin\python.exe"
            )
        )
        if defined BASE_PY (
            call %BASE_PY% -m venv .venv
        ) else (
            python -m venv .venv
        )
    )
) else (
    echo [1/4] Virtual environment .venv already exists.
)

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python could not create .venv. Install Python 3.11+ first.
    pause
    exit /b 1
)

echo [2/4] Installing project dependencies from requirements.txt ...
where uv >nul 2>&1
if not errorlevel 1 (
    uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
) else (
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
if errorlevel 1 (
    echo [ERROR] Dependency installation failed!
    pause
    exit /b 1
)

echo [3/4] Ensuring storage directories exist ...
for %%D in (
    "Z-PACT-main\storage"
    "Z-PACT-main\storage\materials"
    "Z-PACT-main\storage\originals"
    "Z-PACT-main\storage\covers"
    "Z-PACT-main\storage\frames"
    "Z-PACT-main\storage\logos"
    "Z-PACT-main\storage\previews"
    "Z-PACT-main\storage\receipts"
    "Z-PACT-main\storage\stamped"
) do (
    if not exist "%%~D" mkdir "%%~D"
)

if not exist "C:\.data\webpdf_elite.db" (
    if exist "Z-PACT-main\storage\webpdf_elite.db" (
        echo [INFO] Active database found at Z-PACT-main\storage\webpdf_elite.db ^(auto-loaded by config.py^).
    ) else (
        echo [WARN] No pre-populated database found; a fresh DB will be created on first run.
    )
) else (
    echo [INFO] Detected existing C:\.data\webpdf_elite.db on this machine.
)

echo [4/4] Checking cloudflared.exe ^(needed only for HTTPS MiniApp tunnel mode^)...
if not exist "cloudflared.exe" (
    echo Downloading official cloudflared-windows-amd64.exe ...
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'cloudflared.exe' -UseBasicParsing; Write-Host 'Downloaded cloudflared.exe successfully.' } catch { Write-Host '[WARN] Could not download cloudflared.exe automatically. You can still run locally with: .\.venv\Scripts\python.exe orchestrator.py --local' }"
)

echo.
echo ================================================================
echo   Setup Complete!
echo   - Full run ^(with MiniApp tunnel^): تشغيل_محلي_خفيف.bat
echo   - Local/LAN only ^(no tunnel^):     .\.venv\Scripts\python.exe orchestrator.py --local
echo   - Accounts ^& Batch Guide:        Z-PACT_Accounts_And_Batch_Guide.md
echo ================================================================
pause
