# PyInstaller spec: builds a single-file `secdogie-open` executable that
# bundles Python + all dependencies (including secdogie_agent, which it
# drives as a library), so end users don't need Python installed.
#
# Build (from the open/ directory, with ../agent installed into the build
# venv first -- see build.sh):
#   pyinstaller packaging/secdogie-open.spec
# Output:
#   dist/secdogie-open           (Linux/macOS)
#   dist/secdogie-open.exe       (Windows)
#
# The executable is OS- and architecture-specific: build it once per target
# platform (Linux x86_64, macOS arm64, Windows x86_64, ...).

import os

from PyInstaller.utils.hooks import collect_submodules

PACKAGE_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
# secdogie_agent is installed editable (pip install -e ../agent), which uses a
# finder-hook .pth that PyInstaller's static analysis doesn't execute -- so it
# can't discover the real source through that indirection. Point pathex at the
# actual agent/ source tree directly instead.
AGENT_ROOT = os.path.abspath(os.path.join(SPECPATH, "..", "..", "agent"))

# secdogie_open drives secdogie_agent as a library, so its own runtime deps
# (anthropic, pyautogui, mss, pyperclip) need to come along too. pywinctl and
# tkinter both do platform-dependent dynamic imports PyInstaller's static
# analysis can miss.
hidden = (
    collect_submodules("secdogie_open")
    + collect_submodules("secdogie_agent")
    + collect_submodules("anthropic")
    + collect_submodules("pyautogui")
    + collect_submodules("mss")
    + collect_submodules("pyperclip")
    + collect_submodules("pywinctl")
    + collect_submodules("PIL")
)

a = Analysis(
    ["entry.py"],
    pathex=[PACKAGE_ROOT, AGENT_ROOT],
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
    name="secdogie-open",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
