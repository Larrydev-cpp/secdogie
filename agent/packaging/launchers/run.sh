#!/usr/bin/env bash
# ============================================================
#  Launcher for secdogie-agent on Linux.
#  Keep this file next to the secdogie-agent binary.
#  Run it from a terminal:  ./run.sh   (or ./run.sh "a task")
# ============================================================
cd "$(dirname "$0")" || exit 1

if [ -z "$ANTHROPIC_API_KEY" ] && [ ! -f "$HOME/.config/secdogie/config" ]; then
  echo "No API key set up yet -- creating a config file for you to fill in..."
  echo
  ./secdogie-agent --init-config
  echo
  echo "Then edit that file, add your Anthropic API key, and run ./run.sh again."
  exit 0
fi

# --gui pops up a task window on a desktop; falls back to the terminal if
# there's no display. Any argument you pass becomes the task.
exec ./secdogie-agent --gui "$@"
