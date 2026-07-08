"""Stage 2: the aggregator (the "supervisor") fuses the per-view observations
into one consolidated 3D scene.

This is a text-only turn -- it reasons over the workers' structured JSON, not
the raw images -- so it can merge objects seen from several angles, reconcile
or flag disagreements, and estimate a layout no single view could give.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from secdogie_agent.providers.base import parse_action_json

from .analyze import WorkerObservation
from .model import SceneModel

AGGREGATOR_SYSTEM = """You are the lead 3D analyst. You receive several per-view observations of ONE 3D scene, each produced by an analyst who saw a different camera angle. Reconcile them into a single consolidated understanding:
- Merge objects that multiple views describe into one entry (views rarely use identical names -- match by role/position).
- Prefer facts confirmed by more than one view; treat single-view claims as lower confidence.
- Where views conflict, do NOT silently pick one -- record it under "disagreements".
- Estimate each object's place in the 3D layout using the combined evidence (e.g. depth ordering from views that show it).

Return ONLY a JSON object, no prose:
{
  "objects": [
    {"name": "string", "position_3d": "string", "seen_in": ["view labels"], "confidence": 0.0}
  ],
  "layout": "overall spatial arrangement of the scene in words",
  "disagreements": ["where views conflicted, or a fact only one view supports"],
  "summary": "a few sentences describing the reconstructed 3D scene"
}"""


@dataclass
class SceneAnalysis:
    raw_text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    observations: list[WorkerObservation] = field(default_factory=list)


def _aggregator_user(observations: list[WorkerObservation]) -> str:
    parts = []
    for o in observations:
        if o.error:
            parts.append(f"View '{o.label}': ERROR, no observation ({o.error})")
        elif o.data:
            parts.append(f"View '{o.label}':\n{json.dumps(o.data, ensure_ascii=False)}")
        else:
            parts.append(f"View '{o.label}' (unparsed):\n{o.raw_text}")
    joined = "\n\n".join(parts)
    return (
        f"Here are {len(observations)} per-view observations of one 3D scene. "
        f"Consolidate them into a single 3D understanding.\n\n{joined}"
    )


def aggregate(model: SceneModel, observations: list[WorkerObservation]) -> SceneAnalysis:
    text = model.describe(AGGREGATOR_SYSTEM, _aggregator_user(observations), None)  # text-only fusion
    try:
        data = parse_action_json(text)
    except ValueError:
        data = {}
    return SceneAnalysis(raw_text=text, data=data, observations=list(observations))
