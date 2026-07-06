@echo off
REM ============================================================
REM  Double-click launcher for secdogie-agent on Windows.
REM  Keep this file next to secdogie-agent.exe.
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

echo Starting secdogie-agent -- a window will ask what you want it to do.
echo (Close this black window to stop the agent at any time.)
echo.
secdogie-agent.exe --gui
pause
