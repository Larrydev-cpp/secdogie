# Builds the single-file `secdogie-scene3d.exe` on Windows.
# Produces: scene3d\packaging\dist\secdogie-scene3d.exe
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$PkgDir = Resolve-Path (Join-Path $Here "..")
$RepoRoot = Resolve-Path (Join-Path $PkgDir "..")

Set-Location $PkgDir

python -m venv .build-venv
& ".build-venv\Scripts\Activate.ps1"
pip install --upgrade pip | Out-Null
pip install -e "$RepoRoot\agent" | Out-Null
# Bundle both providers so the built binary can use either --provider without
# a rebuild; drop one of these to ship a smaller, single-provider binary.
pip install -e ".[openai]" anthropic pyinstaller | Out-Null

Set-Location $Here
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist
pyinstaller secdogie-scene3d.spec --distpath .\dist --workpath .\build --noconfirm

Write-Host ""
Write-Host "Built: $Here\dist\secdogie-scene3d.exe"
Write-Host "Try:   .\dist\secdogie-scene3d.exe --help"
