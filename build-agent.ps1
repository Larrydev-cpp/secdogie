# Build secdogie-agent.exe (Windows, one file, no Python needed to run).
#
# Put this at the repo root on purpose so you don't have to dig into
# agent\packaging\. Just:
#
#   .\build-agent.ps1
#
# Output:
#   agent\packaging\dist\secdogie-agent.exe
#
# Double-click the .exe → frosted menu → paste API key → run a task.
#
# If PowerShell says "running scripts is disabled":
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\build-agent.ps1
#
# Or from cmd.exe:
#   powershell -ExecutionPolicy Bypass -File build-agent.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Build = Join-Path $Root "agent\packaging\build.ps1"

if (-not (Test-Path $Build)) {
    Write-Error "Missing $Build — are you in the secdogie repo root?"
    exit 1
}

Write-Host "==> building secdogie-agent.exe (this takes a few minutes the first time)"
& powershell -ExecutionPolicy Bypass -File $Build

$Exe = Join-Path $Root "agent\packaging\dist\secdogie-agent.exe"
if (Test-Path $Exe) {
    Write-Host ""
    Write-Host "Done. Your Windows executable is here:"
    Write-Host "  $Exe"
    Write-Host ""
    Write-Host "Double-click it → Set up / edit API key → paste any provider key → Describe a task."
} else {
    Write-Error "Build finished but $Exe was not found."
    exit 1
}
