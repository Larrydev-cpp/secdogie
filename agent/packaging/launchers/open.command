#!/bin/bash
# ============================================================
#  Double-click launcher for secdogie-agent on macOS
#  (Apple Silicon arm64  +  Intel x86_64)
#
#  Keep this file next to the secdogie-agent binary.
#
#  First-time Gatekeeper block?
#    right-click the binary (or this .command) → Open → Open
#    or in Terminal:
#      xattr -d com.apple.quarantine ./secdogie-agent
#      chmod +x ./secdogie-agent ./open.command
# ============================================================
set -euo pipefail

cd "$(dirname "$0")" || exit 1

BINARY="./secdogie-agent"

# --- Remove quarantine attribute if present (common after download) ---
if command -v xattr >/dev/null 2>&1; then
  if xattr -p com.apple.quarantine "$BINARY" >/dev/null 2>&1; then
    echo "Removing macOS quarantine flag from $BINARY ..."
    xattr -d com.apple.quarantine "$BINARY" 2>/dev/null || true
  fi
  if xattr -p com.apple.quarantine "./open.command" >/dev/null 2>&1; then
    xattr -d com.apple.quarantine "./open.command" 2>/dev/null || true
  fi
fi

# Ensure executable bit
chmod +x "$BINARY" "./open.command" 2>/dev/null || true

# --- First-run config ---
if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ ! -f "$HOME/.config/secdogie/config" ]; then
  echo "No API key set up yet — creating a config file for you to fill in..."
  echo
  "$BINARY" --init-config
  echo
  echo "NEXT:"
  echo "  1. Open the config file shown above"
  echo "  2. Paste your API key after ANTHROPIC_API_KEY="
  echo "  3. Save the file, then run open.command again"
  echo
  read -n1 -r -p "Press any key to close..."
  exit 0
fi

echo "Starting secdogie-agent ..."
Echo
echo "NOTE (macOS Accessibility):"
echo "  For element-level control (--desktop-ax) you must grant Accessibility"
echo "  permission to the host that launches this binary:"
echo "    System Settings → Privacy & Security → Accessibility"
echo "  Add Terminal / iTerm / the secdogie-agent binary itself."
echo

"$BINARY" --gui
status=$?

echo
if [ $status -ne 0 ]; then
  echo "secdogie-agent exited with code $status"
fi
read -n1 -r -p "Press any key to close..."
exit $status
