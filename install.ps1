# One-command setup for the secdogie game stack (Windows / PowerShell).
#
# The packages live in this repo and depend on each other but are NOT on PyPI,
# so `pip install secdogie-carjack` on its own fails. This installs them all
# into one venv, in the right order, in a single pip resolve.
#
#   .\install.ps1              # game stack into .\.venv
#   .\install.ps1 -Yolo        # + ultralytics (YOLO detector; large, GPU for real-time)
#   .\install.ps1 -All         # + the non-game packages (scene3d, android, ios, open)
#   .\install.ps1 -Venv PATH   # use/create a venv somewhere else
#
# If PowerShell blocks the script ("running scripts is disabled"), run first:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
param(
    [switch]$Yolo,
    [switch]$All,
    [string]$Venv = ".venv"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
if (-not (Test-Path $Venv)) {
    Write-Host "==> creating venv at $Venv"
    & $py -m venv $Venv
}
& "$Venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip | Out-Null

# Dependency order (handoff has no local deps; aim needs handoff; carjack needs
# aim). All in one pip invocation so the resolver satisfies them locally.
$pkgs = @(".\handoff", ".\agent", ".\aim", ".\carjack", ".\gta", ".\commander")
if ($All) { $pkgs += @(".\scene3d", ".\android", ".\ios", ".\open") }

Write-Host "==> installing: $($pkgs -join ' ')"
$editable = @()
foreach ($p in $pkgs) { $editable += @("-e", $p) }
python -m pip install @editable

if ($Yolo) {
    Write-Host "==> installing ultralytics (YOLO). Large; a GPU makes it real-time."
    python -m pip install ultralytics
    Write-Host "    a stock yolov8n.pt (knows 'car', 'airplane', 'person', ...) auto-downloads on first use."
}

Write-Host ""
Write-Host "Done. Activate the venv, then try (single-player games only):"
Write-Host ""
Write-Host "  $Venv\Scripts\Activate.ps1"
Write-Host "  secdogie-carjack --weights yolov8n.pt --label car --enter-key f"
Write-Host "  secdogie-aim engage --weights dragon.pt --label ender_dragon --gain 0.4 --no-baton"
Write-Host ""
Write-Host "Installed: secdogie-agent, secdogie-aim, secdogie-carjack, secdogie-gta, secdogie-commander."
