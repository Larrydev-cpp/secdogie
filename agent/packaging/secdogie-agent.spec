# PyInstaller spec: builds a single-file `secdogie-agent` executable that
# bundles Python + all dependencies, so end users don't need Python installed.
#
# Build (from the agent/ directory):
#   pyinstaller packaging/secdogie-agent.spec
# Output:
#   dist/secdogie-agent           (Linux/macOS)
#   dist/secdogie-agent.exe       (Windows)
#
# The executable is OS- and architecture-specific: build it once per target
# platform (Linux x86_64, macOS arm64, Windows x86_64, ...). See
# packaging/README.md.

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

# Windows: build a windowed (GUI-subsystem) exe so a double-click shows the
# frosted-glass menu with NO black console box behind it. The windowed build's
# safety nets (crash dialog, secdogie.log, reattach-to-parent-console for
# terminal/--help use) live in secdogie_agent/frozen_runtime.py. On Linux/macOS
# the flag has no black-box downside (they run from a terminal via run.sh /
# open.command), so keep a console there for live logs and simplest behavior.
WINDOWED = sys.platform.startswith("win")

# Directory containing the agent/ package source (this spec lives in
# agent/packaging/, so the package root is one level up). Putting it on
# pathex lets PyInstaller find the secdogie_agent source directly, without
# relying on how it happens to be installed in the build environment.
PACKAGE_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# anthropic and pyautogui both do dynamic/lazy imports that PyInstaller's
# static analysis can miss; pull their whole package trees in explicitly.
hidden = (
    collect_submodules("secdogie_agent")
    + collect_submodules("anthropic")
    + collect_submodules("pyautogui")
    + collect_submodules("mss")
    + collect_submodules("pyperclip")
)

a = Analysis(
    ["entry.py"],
    pathex=[PACKAGE_ROOT],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="secdogie-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=not WINDOWED,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
