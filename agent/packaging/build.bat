@echo off
setlocal

rem Builds the single-file `secdogie-agent.exe` for Windows.
rem (Windows counterpart of build.sh -- same steps, same layout.)
rem
rem Run from anywhere; it operates relative to its own location. Produces:
rem   agent\packaging\dist\secdogie-agent.exe
rem
rem The resulting binary is OS/architecture specific -- run this on each
rem target platform you want to ship a binary for.

set "HERE=%~dp0"
set "AGENT_DIR=%HERE%.."

rem Pick a Python: prefer the py launcher (python.org installs), else PATH.
set "PYTHON=py -3"
%PYTHON% -c "import sys" >nul 2>&1 || set "PYTHON=python"
%PYTHON% -c "import sys" >nul 2>&1 || (
    echo error: no Python found. Install Python 3.10+ from python.org, then re-run.
    exit /b 1
)

cd /d "%AGENT_DIR%" || exit /b 1

rem Use an isolated build venv so the frozen binary only contains this
rem project's real dependencies, not whatever else is in a dev environment.
%PYTHON% -m venv .build-venv || exit /b 1
set "VENV_PY=%CD%\.build-venv\Scripts\python.exe"

"%VENV_PY%" -m pip install --upgrade pip >nul || exit /b 1
"%VENV_PY%" -m pip install -e . pyinstaller >nul || exit /b 1

cd /d "%HERE%" || exit /b 1
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
"%VENV_PY%" -m PyInstaller secdogie-agent.spec --distpath .\dist --workpath .\build --noconfirm || exit /b 1

echo.
echo Built: %HERE%dist\secdogie-agent.exe
echo Try:   "%HERE%dist\secdogie-agent.exe" --help
endlocal
