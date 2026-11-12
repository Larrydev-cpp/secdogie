# PyInstaller spec: secdogie Studio as one folder per platform, with Python,
# Qt (Qt Quick) and the node inside, so nothing else needs to be installed.
#
# Build (from the studio/ directory, with `pip install -e ".[gui]" pyinstaller`
# and the other secdogie packages installed):
#   pyinstaller packaging/secdogie-studio.spec --noconfirm
# Output:
#   dist/secdogie-studio/            Linux and Windows: run secdogie-studio[.exe]
#   dist/secdogie Studio.app         macOS
#
# One binary does everything: the window, `--self-test`, and the node child
# (`--node-child`), so a frozen Studio starts its node without a Python
# installation (studio/secdogie_studio/nodeproc.py). A folder rather than one
# file: Qt is large, and unpacking it on every start would be slow. UPX stays
# off: packed binaries are a common antivirus false positive.

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
REPO = os.path.dirname(ROOT)
# Every secdogie package Studio carries, straight from the source tree, so the
# build does not depend on how they were installed (an editable install hides
# them from PyInstaller's analysis).
SOURCES = [os.path.join(REPO, d) for d in ("studio", "node", "dialogue", "citadel", "policy", "transport", "identity")]
WINDOWED = sys.platform in ("win32", "darwin")  # no console window behind the app

datas = collect_data_files("secdogie_studio", includes=["qml/*.qml", "qml/qmldir"])
hidden = (
    collect_submodules("secdogie_studio")
    + collect_submodules("secdogie_node")
    + collect_submodules("secdogie_transport")
    # PyNaCl (keys, sealing) loads its C bindings through cffi, which static analysis does not see
    + collect_submodules("nacl")
    + ["_cffi_backend"]
    # WebRTC with your other devices: aiortc and its ICE / SCTP / DTLS stack import lazily
    + collect_submodules("aiortc")
    + collect_submodules("aioice")
    + collect_submodules("websockets")
)
datas += collect_data_files("aiortc")
ICON_DIR = os.path.join(SPECPATH, "icons")
ICON = os.path.join(ICON_DIR, "secdogie-studio.ico" if sys.platform == "win32" else "secdogie-studio.png")

a = Analysis(
    ["entry.py"],
    pathex=SOURCES,
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Never used by Studio; leaving them out keeps the bundle smaller.
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="secdogie-studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=not WINDOWED,
    icon=ICON,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="secdogie-studio")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="secdogie Studio.app",
        icon=ICON,  # PyInstaller turns the PNG into .icns (needs Pillow)
        bundle_identifier="dev.secdogie.studio",
        info_plist={
            "CFBundleName": "secdogie Studio",
            "CFBundleDisplayName": "secdogie Studio",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
