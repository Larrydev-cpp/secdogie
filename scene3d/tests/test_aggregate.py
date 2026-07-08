from conftest import FakeModel

from secdogie_scene3d.aggregate import AGGREGATOR_SYSTEM, aggregate
from secdogie_scene3d.analyze import WorkerObservation


def _obs(label, data=None, error=None, raw=""):
    return WorkerObservation(label=label, data=data or {}, error=error, raw_text=raw)


def test_aggregate_is_text_only_and_sees_every_view():
    model = FakeModel('{"objects": [{"name": "cube", "seen_in": ["front", "top"]}], "summary": "one cube"}')
    observations = [
        _obs("front", {"objects": [{"name": "cube"}]}),
        _obs("top", {"objects": [{"name": "box"}]}),
    ]
    result = aggregate(model, observations)

    system, user, image = model.calls[0]
    assert image is None  # fusion reasons over the JSON, not the images
    assert system == AGGREGATOR_SYSTEM
    assert "front" in user and "top" in user
    assert result.data["summary"] == "one cube"
    assert result.observations == observations


def test_aggregate_reports_errored_views_to_the_model():
    model = FakeModel('{"disagreements": []}')
    observations = [
        _obs("front", {"objects": []}),
        _obs("top", error="RuntimeError: rate limited"),
    ]
    aggregate(model, observations)
    _system, user, _image = model.calls[0]
    assert "ERROR" in user and "rate limited" in user


def test_aggregate_unparseable_reply_yields_empty_data_with_raw_text():
    result = aggregate(FakeModel("the scene has a cube and a sphere"), [_obs("front", {"o": 1})])
    assert result.data == {}
    assert result.raw_text == "the scene has a cube and a sphere"
