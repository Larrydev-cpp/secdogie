#!/usr/bin/env bash
# Builds the single-file `secdogie-agent` executable for the current OS.
#
# Run from anywhere; it operates relative to its own location. Produces:
#   agent/packaging/dist/secdogie-agent        (Linux/macOS)
#   agent/packaging/dist/secdogie-agent.exe    (Windows, when run there)
#
# The resulting binary is OS/architecture specific -- run this on each
# target platform you want to ship a binary for.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$HERE/.." && pwd)"

cd "$AGENT_DIR"

# Use an isolated build venv so the frozen binary only contains this
# project's real dependencies, not whatever else is in a dev environment.
python3 -m venv .build-venv
# shellcheck disable=SC1091
source .build-venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -e . pyinstaller >/dev/null

cd "$HERE"
rm -rf build dist
pyinstaller secdogie-agent.spec --distpath ./dist --workpath ./build --noconfirm

echo
echo "Built: $HERE/dist/secdogie-agent"
echo "Try:   ./dist/secdogie-agent --help"
