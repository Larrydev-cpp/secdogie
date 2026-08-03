@echo off
REM ============================================================
REM  Double-click launcher for secdogie-agent on Windows.
REM  Keep this file next to secdogie-agent.exe.
REM
REM  You can also just double-click secdogie-agent.exe itself:
REM  launched with no arguments it shows the same selection
REM  window. This .bat only adds a console that stays open, so
REM  you can read the log and stop the agent by closing it.
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

echo A selection window will open -- pick what the agent should do.
echo (Close this black window to stop the agent at any time.)
echo.
secdogie-agent.exe
pause
