# Build the portable PC app into dist\MusicFrame.exe.
# GNU toolchain (pc-rs\rust-toolchain.toml): no Visual Studio needed.
# Once: rustup component add llvm-tools --toolchain stable-x86_64-pc-windows-gnu
param([string[]]$Cargo = @('build', '--release'))
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$rust = "$env:USERPROFILE\.rustup\toolchains\stable-x86_64-pc-windows-gnu\lib\rustlib\x86_64-pc-windows-gnu\bin"

# the dlltool shipped with the GNU toolchain needs an assembler it does not have; llvm-ar under this name does not
$dlltool = "$root\pc-rs\target\tools\llvm-dlltool.exe"
if (-not (Test-Path $dlltool)) {
    New-Item -ItemType Directory -Force (Split-Path $dlltool) | Out-Null
    Copy-Item "$rust\llvm-ar.exe" $dlltool
}
$env:PATH = "$rust\self-contained;$env:PATH"
$env:RUSTFLAGS = "-Cdlltool=$($dlltool -replace '\\', '/') -Ctarget-feature=+crt-static"

Push-Location "$root\pc-rs"
try {
    & cargo @Cargo
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
} finally { Pop-Location }

if ($Cargo -contains 'build') {
    New-Item -ItemType Directory -Force "$root\dist" | Out-Null
    Copy-Item "$root\pc-rs\target\release\MusicFrame.exe" "$root\dist\MusicFrame.exe" -Force
    '{0:N1} MB  {1}' -f ((Get-Item "$root\dist\MusicFrame.exe").Length / 1MB), "$root\dist\MusicFrame.exe"
}
