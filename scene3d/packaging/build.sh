#!/usr/bin/env bash
# Builds the single-file `secdogie-scene3d` executable for the current OS.
#
# Produces:
#   scene3d/packaging/dist/secdogie-scene3d        (Linux/macOS)
#   scene3d/packaging/dist/secdogie-scene3d.exe    (Windows, when run there)
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
# Bundle both providers so the built binary can use either --provider without
# a rebuild; drop one of these lines to ship a smaller, single-provider binary.
pip install -e '.[openai]' anthropic pyinstaller >/dev/null

cd "$HERE"
rm -rf build dist
pyinstaller secdogie-scene3d.spec --distpath ./dist --workpath ./build --noconfirm

echo
echo "Built: $HERE/dist/secdogie-scene3d"
echo "Try:   ./dist/secdogie-scene3d --help"
