# Build the screen firmware and send it: -Action build | usb | wifi
param([ValidateSet('build', 'usb', 'wifi')][string]$Action = 'build')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

if (-not (Test-Path "$root\.env")) { Write-Host "No .env yet: run setup first." -ForegroundColor Yellow; exit 1 }
if (-not (Test-Path "$root\.venv\Scripts\pio.exe")) { Write-Host "PlatformIO missing: run setup first." -ForegroundColor Yellow; exit 1 }

# The ESP32 libraries have paths longer than Windows allows; give PlatformIO a short drive letter.
$core = "$root\.platformio"
New-Item -ItemType Directory -Force $core | Out-Null
$maps = @(cmd /c subst)
$drive = $null
foreach ($l in 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W') {
    $m = $maps | Where-Object { $_ -like "${l}:\*" }
    if ($m -and ($m -like "*=> $core")) { $drive = $l; break }
    if (-not $m -and -not (Test-Path "${l}:\")) { cmd /c subst "${l}:" "$core" | Out-Null; $drive = $l; break }
}
if (-not $drive) { throw "No free drive letter between P and W" }
$env:PLATFORMIO_CORE_DIR = "${drive}:\"
$pio = "$root\.venv\Scripts\pio.exe"

Push-Location "$root\firmware"
try {
    Write-Host "Building the firmware (the first time downloads ~1 GB of tools, be patient)..."
    & $pio run -e esp32s3
    if ($LASTEXITCODE) { throw "Build failed" }
    if ($Action -eq 'usb') {
        Write-Host "Flashing over USB..."
        & $pio run -e esp32s3 -t upload
        if ($LASTEXITCODE) { throw "Flash failed. Hold BOOT on the board, press and release RESET, release BOOT, then try again." }
    }
    elseif ($Action -eq 'wifi') {
        $env:PYTHONPATH = "$root\pc"
        & "$root\pc\.venv\Scripts\python.exe" -m musicframe.ota "$root\firmware\.pio\build\esp32s3\firmware.bin"
        if ($LASTEXITCODE) { throw "Update over WiFi failed" }
    }
    Write-Host "Done." -ForegroundColor Green
}
finally { Pop-Location }
