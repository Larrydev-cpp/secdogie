"""Entry point for the PyInstaller-built single-file executable.

PyInstaller freezes a *script*, not a console_scripts entry point, so this
tiny launcher just forwards to the same main() that the `secdogie-agent`
pip console script uses.
"""
import sys

from secdogie_agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
