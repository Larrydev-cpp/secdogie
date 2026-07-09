#!/usr/bin/env bash
# Builds the single-file `secdogie-android` executable for the current OS.
# Still needs `adb` on PATH at run time -- see android/README.md.
#
# Produces:
#   android/packaging/dist/secdogie-android        (Linux/macOS)
#   android/packaging/dist/secdogie-android.exe    (Windows, when run there)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$PKG_DIR/.." && pwd)"

cd "$PKG_DIR"

python3 -m venv .build-venv
# shellcheck disable=SC1091
source .build-venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -e "$REPO_ROOT/agent" >/dev/null
pip install -e . pyinstaller >/dev/null

cd "$HERE"
rm -rf build dist
pyinstaller secdogie-android.spec --distpath ./dist --workpath ./build --noconfirm

echo
echo "Built: $HERE/dist/secdogie-android"
echo "Try:   ./dist/secdogie-android --help"
