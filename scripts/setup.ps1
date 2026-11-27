# One-time setup: tools for the firmware (PlatformIO), the PC app, and the .env settings file.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python is not installed. Get it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run setup again." -ForegroundColor Yellow
    exit 1
}

Write-Host "[1/3] Firmware tools (PlatformIO)..."
if (-not (Test-Path "$root\.venv")) { python -m venv "$root\.venv" }
& "$root\.venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check --upgrade platformio
if ($LASTEXITCODE) { throw "Could not install PlatformIO" }

Write-Host "[2/3] PC app..."
if (-not (Test-Path "$root\pc\.venv")) { python -m venv "$root\pc\.venv" }
& "$root\pc\.venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r "$root\pc\requirements.txt"
if ($LASTEXITCODE) { throw "Could not install the PC app" }

Write-Host "[3/3] Settings file..."
if (-not (Test-Path "$root\.env")) {
    Copy-Item "$root\.env.example" "$root\.env"
    Write-Host "Fill in your WiFi name, WiFi password and an update password in the window that opens, then save." -ForegroundColor Yellow
    Start-Process notepad "$root\.env" -Wait
}
Write-Host "Setup done." -ForegroundColor Green
