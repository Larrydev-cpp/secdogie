# PyInstaller spec: builds a single-file `secdogie-android` executable that
# bundles Python + all dependencies (including secdogie_agent, which it
# drives as a library), so end users don't need Python installed. It still
# needs `adb` on PATH at run time -- that's a separate system tool, not
# something PyInstaller bundles.
#
# Build (from the android/ directory, with ../agent installed into the build
# venv first -- see build.sh):
#   pyinstaller packaging/secdogie-android.spec
# Output:
#   dist/secdogie-android           (Linux/macOS)
#   dist/secdogie-android.exe       (Windows)

import os

from PyInstaller.utils.hooks import collect_submodules

PACKAGE_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
# secdogie_agent is installed editable (pip install -e ../agent), which uses a
# finder-hook .pth that PyInstaller's static analysis doesn't execute -- so it
# can't discover the real source through that indirection. Point pathex at the
# actual agent/ source tree directly instead.
AGENT_ROOT = os.path.abspath(os.path.join(SPECPATH, "..", "..", "agent"))

hidden = (
    collect_submodules("secdogie_android")
    + collect_submodules("secdogie_agent")
    + collect_submodules("anthropic")
    + collect_submodules("pyautogui")
    + collect_submodules("mss")
    + collect_submodules("pyperclip")
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
    name="secdogie-android",
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
