"""secdogie-node: the resident node process (see ``node.py`` and ``cli.py``)."""
from __future__ import annotations

from .node import Node, NodeConfig

__version__ = "0.1.0"

__all__ = ["Node", "NodeConfig"]
