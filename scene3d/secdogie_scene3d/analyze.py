"""Stage 1: each worker analyzes ONE viewpoint, concurrently.

Workers run in parallel and, when given a pool of models (one per API key),
are spread round-robin across the pool, so nine views can be nine in-flight
requests over nine keys instead of nine serial calls on one. A worker whose
call fails is captured as an error observation rather than sinking the batch --
the aggregator is told which views dropped out.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from secdogie_agent.providers.base import parse_action_json  # tolerant "first JSON object" extractor

from .model import SceneModel
from .views import Viewpoint

WORKER_SYSTEM = """You are one of several vision analysts. Each analyst sees exactly ONE view of the SAME 3D scene, from a different camera angle. Report only what YOUR view supports -- do not invent objects or relationships you cannot actually see from this angle, and mark anything uncertain.

Return ONLY a JSON object, no prose:
{
  "objects": [
    {"name": "string", "position": "where in the frame / scene", "depth": "near|mid|far|unknown", "size": "string", "confidence": 0.0}
  ],
  "relations": ["spatial relations visible from here, e.g. 'the cube is left of and in front of the sphere'"],
  "occlusions": ["what hides what from this angle"],
  "notes": "anything else relevant, including what this angle can't determine"
}"""


@dataclass
class WorkerObservation:
    label: str
    raw_text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _worker_user(vp: Viewpoint) -> str:
    hint = f"\nCamera / view description: {vp.hint}" if vp.hint else ""
    return (
        f"This is the '{vp.label}' view of the scene.{hint}\n"
        "Describe the 3D scene as seen from this single view, as JSON."
    )


def analyze_view(model: SceneModel, vp: Viewpoint) -> WorkerObservation:
    """Run one worker over one viewpoint. Any model/parse failure is returned
    as an error observation, never raised."""
    try:
        text = model.describe(WORKER_SYSTEM, _worker_user(vp), vp.image_png)
    except Exception as e:
        return WorkerObservation(label=vp.label, error=f"{type(e).__name__}: {e}")
    try:
        data = parse_action_json(text)
    except ValueError:
        data = {}  # keep the raw text; the aggregator can still use it
    return WorkerObservation(label=vp.label, raw_text=text, data=data)


def run_workers(
    models: SceneModel | list[SceneModel],
    viewpoints: list[Viewpoint],
    max_workers: int | None = None,
) -> list[WorkerObservation]:
    """Analyze every viewpoint, in parallel, returning observations in the same
    order as `viewpoints`. `models` may be a single model or a pool; view i is
    handled by pool[i % len(pool)]."""
    pool = models if isinstance(models, list) else [models]
    if not pool:
        raise ValueError("run_workers needs at least one model")
    if not viewpoints:
        return []
    workers = max_workers if max_workers is not None else min(len(pool), len(viewpoints))

    def fn(i: int) -> WorkerObservation:
        return analyze_view(pool[i % len(pool)], viewpoints[i])

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return list(ex.map(fn, range(len(viewpoints))))  # map preserves input order
