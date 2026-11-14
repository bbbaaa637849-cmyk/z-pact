# Z-PACT Enterprise - Smart Android APK Compiler (PowerShell Wrapper)
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
$PythonExe = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

& $PythonExe (Join-Path $ScriptDir "build_apk_engine.py")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
