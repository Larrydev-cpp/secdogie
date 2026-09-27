"""Perception adapters: pure in-memory conversions from structural senses
(the accessibility tree) into the typed ``observation.Observation`` model."""
from __future__ import annotations

from .adapter import BaseObservationAdapter, StructuralObservationAdapter

__all__ = ["BaseObservationAdapter", "StructuralObservationAdapter"]
