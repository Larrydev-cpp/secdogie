#!/usr/bin/env bash
# Builds the single-file `secdogie-open` executable for the current OS.
#
# Run from anywhere; it operates relative to its own location. Produces:
#   open/packaging/dist/secdogie-open        (Linux/macOS)
#   open/packaging/dist/secdogie-open.exe    (Windows, when run there)
#
# The resulting binary is OS/architecture specific -- run this on each
# target platform you want to ship a binary for.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$PKG_DIR/.." && pwd)"

cd "$PKG_DIR"

# Isolated build venv so the frozen binary only contains real dependencies,
# not whatever else is in a dev environment. secdogie_open drives
# secdogie_agent as a library, so it's installed too.
python3 -m venv .build-venv
# shellcheck disable=SC1091
source .build-venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -e "$REPO_ROOT/agent" >/dev/null
pip install -e . pyinstaller >/dev/null

cd "$HERE"
rm -rf build dist
pyinstaller secdogie-open.spec --distpath ./dist --workpath ./build --noconfirm

echo
echo "Built: $HERE/dist/secdogie-open"
echo "Try:   ./dist/secdogie-open"
