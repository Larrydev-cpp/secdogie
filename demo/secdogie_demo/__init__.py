"""secdogie-demo: a reproducible headless walkthrough that chains all four
pillars -- decentralized mesh, distributed learning, the Socratic gate, and
structured authenticated device action -- with real components (only the vision
model and the browser are faked, as in fleet's end-to-end test)."""
from __future__ import annotations

from .vertical_slice import SliceResult, run_slice

__version__ = "0.1.0"
__all__ = ["SliceResult", "run_slice"]
