# Builds the single-file `secdogie-open.exe` on Windows.
# Produces: open\packaging\dist\secdogie-open.exe
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$PkgDir = Resolve-Path (Join-Path $Here "..")
$RepoRoot = Resolve-Path (Join-Path $PkgDir "..")

Set-Location $PkgDir

python -m venv .build-venv
& ".build-venv\Scripts\Activate.ps1"
pip install --upgrade pip | Out-Null
pip install -e "$RepoRoot\agent" | Out-Null
pip install -e . pyinstaller | Out-Null

Set-Location $Here
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist
pyinstaller secdogie-open.spec --distpath .\dist --workpath .\build --noconfirm

Write-Host ""
Write-Host "Built: $Here\dist\secdogie-open.exe"
Write-Host "Try:   .\dist\secdogie-open.exe"
