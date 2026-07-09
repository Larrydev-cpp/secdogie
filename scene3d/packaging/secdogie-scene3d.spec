# PyInstaller spec: builds a single-file `secdogie-scene3d` executable.
#
# scene3d only reuses secdogie_agent's config resolution and tolerant JSON
# parser (both lightweight, no pyautogui/mss/anthropic import at module load
# time -- those are lazy-imported inside secdogie_agent's provider classes),
# so this bundle stays much smaller than agent/open/android/ios's.
#
# Build (from the scene3d/ directory, with ../agent installed into the build
# venv first -- see build.sh):
#   pyinstaller packaging/secdogie-scene3d.spec
# Output:
#   dist/secdogie-scene3d           (Linux/macOS)
#   dist/secdogie-scene3d.exe       (Windows)

import os

from PyInstaller.utils.hooks import collect_submodules

PACKAGE_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
# secdogie_agent is installed editable (pip install -e ../agent), which uses a
# finder-hook .pth that PyInstaller's static analysis doesn't execute -- so it
# can't discover the real source through that indirection. Point pathex at the
# actual agent/ source tree directly instead.
AGENT_ROOT = os.path.abspath(os.path.join(SPECPATH, "..", "..", "agent"))

# anthropic/openai are optional at run time (the model.py adapters import
# them lazily); collect_submodules is a no-op if a package isn't installed in
# the build venv, so this only bundles whichever you `pip install`ed.
hidden = (
    collect_submodules("secdogie_scene3d")
    + collect_submodules("secdogie_agent")
    + collect_submodules("anthropic")
    + collect_submodules("openai")
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
    name="secdogie-scene3d",
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
