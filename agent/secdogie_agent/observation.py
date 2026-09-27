"""Compatibility path. The observation model moved to ``secdogie_agent.perception``
(zero-screenshot perception: AX + the Direct Inspection Buffer). Import from
there; this module only re-exports the same objects."""
from __future__ import annotations

from .perception.observation import *  # noqa: F401,F403
from .perception.observation import __all__  # noqa: F401
