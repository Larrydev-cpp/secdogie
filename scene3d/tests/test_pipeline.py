from conftest import FakeModel, view
from secdogie_scene3d.pipeline import analyze_scene


def test_analyze_scene_runs_workers_then_aggregates():
    # Workers each report a cube; a separate aggregator model fuses them.
    workers = FakeModel('{"objects": [{"name": "cube"}]}')
    aggregator = FakeModel('{"objects": [{"name": "cube", "seen_in": ["front", "top", "side"]}], "summary": "one cube from 3 views"}')
    views = [view("front"), view("top"), view("side")]

    result = analyze_scene(workers, aggregator, views)

    # aggregator produced the consolidated scene
    assert result.data["summary"] == "one cube from 3 views"
    # every view is represented in the per-view observations, in order
    assert [o.label for o in result.observations] == ["front", "top", "side"]
    assert all(o.data == {"objects": [{"name": "cube"}]} for o in result.observations)
    # workers were called once per view; aggregator exactly once
    assert len(workers.calls) == 3
    assert len(aggregator.calls) == 1


def test_analyze_scene_with_a_worker_pool_spreads_views():
    pool = [FakeModel(f'{{"by": "{n}"}}', name=n) for n in ("k0", "k1")]
    aggregator = FakeModel('{"summary": "done"}')
    views = [view(f"v{i}") for i in range(3)]

    result = analyze_scene(pool, aggregator, views)
    assert [o.data["by"] for o in result.observations] == ["k0", "k1", "k0"]
    assert result.data["summary"] == "done"
