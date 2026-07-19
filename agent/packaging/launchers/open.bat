@echo off
REM ============================================================
REM  Double-click launcher for secdogie-agent on Windows.
REM  Keep this file next to secdogie-agent.exe (and menu.ps1).
REM ============================================================
cd /d "%~dp0"
title secdogie-agent

REM First run with no API key anywhere: help the user create a config file.
if "%ANTHROPIC_API_KEY%"=="" if not exist "%USERPROFILE%\.config\secdogie\config" (
  echo No API key set up yet -- creating a config file for you to fill in...
  echo.
  secdogie-agent.exe --init-config
  echo.
  echo NEXT: open the file shown above, paste your Anthropic API key after
  echo       ANTHROPIC_API_KEY=  , save it, then double-click open.bat again.
  echo.
  pause
  exit /b 0
)

REM Show the liquid-glass selection window (menu.ps1) and capture the one line
REM it prints -- the secdogie-agent arguments for the chosen action. If it's not
REM there or PowerShell can't run it, fall back to plain --gui so the launcher
REM still works everywhere.
set "CHOICE="
if exist "%~dp0menu.ps1" (
  for /f "usebackq delims=" %%c in (`powershell -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0menu.ps1" 2^>nul`) do set "CHOICE=%%c"
) else (
  set "CHOICE=--gui"
)

REM Window closed / cancelled -> nothing chosen -> exit quietly.
if not defined CHOICE exit /b 0

echo Starting: secdogie-agent.exe %CHOICE%
echo (Close this black window to stop the agent at any time.)
echo.
secdogie-agent.exe %CHOICE%
pause
