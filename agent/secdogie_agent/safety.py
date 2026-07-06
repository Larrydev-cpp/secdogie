"""Logging and human-in-the-loop confirmation helpers.

This module is the agent's only line of defense against a model doing
something the user didn't intend -- keep it boring and dependable.
"""
from __future__ import annotations

import logging
import sys


def setup_logging(log_path: str | None) -> logging.Logger:
    logger = logging.getLogger("secdogie_agent")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    if log_path:
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger


def confirm(prompt: str) -> bool:
    """Blocking y/N prompt on stdin. Defaults to No (including on EOF, e.g.
    when stdin isn't a terminal) so an unattended run without --auto fails
    closed rather than silently approving everything."""
    try:
        reply = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return reply in ("y", "yes")
