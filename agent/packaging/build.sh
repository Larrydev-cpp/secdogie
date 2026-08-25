#!/usr/bin/env bash
# Builds the single-file `secdogie-agent` executable for the current OS.
#
# Run from anywhere; it operates relative to its own location. Produces:
#   agent/packaging/dist/secdogie-agent        (Linux/macOS)
#   agent/packaging/dist/secdogie-agent.exe    (Windows, when run there)
#
# The resulting binary is OS/architecture specific -- run this on each
# target platform you want to ship a binary for.
#
# On macOS it also copies open.command next to the binary so a double-click
# launch is ready immediately.
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

# On macOS, place the double-click launcher next to the binary for convenience.
if [[ "$(uname -s)" == "Darwin" ]]; then
  cp -f "$HERE/launchers/open.command" "$HERE/dist/open.command"
  chmod +x "$HERE/dist/open.command" "$HERE/dist/secdogie-agent"
  arch="$(uname -m)"
  echo
  echo "Built for macOS ($arch):"
  echo "  $HERE/dist/secdogie-agent"
  echo "  $HERE/dist/open.command   ← double-click this"
  echo
  echo "First run tips:"
  echo "  • Gatekeeper: right-click → Open, or: xattr -d com.apple.quarantine ./secdogie-agent"
  echo "  • Accessibility: System Settings → Privacy & Security → Accessibility"
else
  echo
  echo "Built: $HERE/dist/secdogie-agent"
  echo "Try:   ./dist/secdogie-agent --help"
fi
