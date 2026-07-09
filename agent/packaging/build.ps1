# Builds the single-file `secdogie-agent.exe` on Windows.
#
# Run from anywhere (PowerShell or cmd via `powershell -File build.ps1`); it
# operates relative to its own location. Produces:
#   agent\packaging\dist\secdogie-agent.exe
#
# The resulting binary is Windows-specific -- see build.sh for Linux/macOS.
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$AgentDir = Resolve-Path (Join-Path $Here "..")

Set-Location $AgentDir

# Isolated build venv so the frozen binary only contains this project's real
# dependencies, not whatever else is in a dev environment.
python -m venv .build-venv
& ".build-venv\Scripts\Activate.ps1"
pip install --upgrade pip | Out-Null
pip install -e . pyinstaller | Out-Null

Set-Location $Here
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist
pyinstaller secdogie-agent.spec --distpath .\dist --workpath .\build --noconfirm

Write-Host ""
Write-Host "Built: $Here\dist\secdogie-agent.exe"
Write-Host "Try:   .\dist\secdogie-agent.exe --help"
