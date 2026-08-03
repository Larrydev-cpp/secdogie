#!/bin/bash
# ============================================================
#  Double-click launcher for secdogie-agent on macOS.
#  Keep this file next to the secdogie-agent binary.
#  (If macOS blocks it: right-click -> Open the first time,
#   or run `chmod +x open.command` in Terminal.)
# ============================================================
cd "$(dirname "$0")" || exit 1

if [ -z "$ANTHROPIC_API_KEY" ] && [ ! -f "$HOME/.config/secdogie/config" ]; then
  echo "No API key set up yet -- creating a config file for you to fill in..."
  echo
  ./secdogie-agent --init-config
  echo
  echo "NEXT: open the file shown above, paste your Anthropic API key after"
  echo "      ANTHROPIC_API_KEY=  , save it, then run open.command again."
  read -n1 -r -p "Press any key to close..."
  exit 0
fi

echo "Starting secdogie-agent -- a window will ask what you want it to do."
./secdogie-agent --gui
read -n1 -r -p "Press any key to close..."
