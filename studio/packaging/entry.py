"""Entry point for the frozen Studio build (PyInstaller). The same binary is the
window, the --self-test, and the node child (--node-child); see cli.py."""
import sys

from secdogie_studio.cli import main

if __name__ == "__main__":
    sys.exit(main())
