"""Wire the two stages together: workers analyze each view in parallel, then
the aggregator fuses their observations into one 3D scene."""
from __future__ import annotations

from .aggregate import SceneAnalysis, aggregate
from .analyze import run_workers
from .model import SceneModel
from .views import Viewpoint


def analyze_scene(
    worker_models: SceneModel | list[SceneModel],
    aggregator_model: SceneModel,
    viewpoints: list[Viewpoint],
    max_workers: int | None = None,
) -> SceneAnalysis:
    """N workers (spread over the model pool) analyze the N viewpoints
    concurrently; the aggregator then consolidates their observations."""
    observations = run_workers(worker_models, viewpoints, max_workers=max_workers)
    return aggregate(aggregator_model, observations)
