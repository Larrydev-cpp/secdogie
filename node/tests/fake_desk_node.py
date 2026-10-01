"""Test launcher: the real ``secdogie-node`` command, with the scripted model and
the fake desktop from ``fakes.py`` patched in -- so a multi-process end-to-end
test can run the node as its own OS process. Not a production entry point."""
from __future__ import annotations

import os
import sys

import fakes
from secdogie_node.cli import main

if __name__ == "__main__":
    fakes.install(setattr, plan=os.environ.get("SECDOGIE_FAKE_PLAN", "report"))
    sys.exit(main(sys.argv[1:]))
