from __future__ import annotations

import sys

from .gui import main as gui_main


def main(argv: list[str] | None = None) -> int:
    # No flags yet -- everything (task, model, window selection) is entered
    # in the GUI itself. A CLI surface can grow here if a non-interactive
    # mode turns out to be wanted later.
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
