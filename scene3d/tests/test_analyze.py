from conftest import FakeModel, view

from secdogie_scene3d.analyze import WORKER_SYSTEM, analyze_view, run_workers


def test_analyze_view_parses_json_and_passes_the_image():
    model = FakeModel('{"objects": [{"name": "cube"}], "notes": "ok"}')
    obs = analyze_view(model, view("front", data=b"IMG"))
    assert obs.label == "front"
    assert obs.data == {"objects": [{"name": "cube"}], "notes": "ok"}
    assert obs.error is None
    # the worker got the image and the worker system prompt
    system, user, image = model.calls[0]
    assert system == WORKER_SYSTEM
    assert image == b"IMG"
    assert "'front' view" in user


def test_analyze_view_tolerates_prose_wrapped_json():
    model = FakeModel('Sure! ```json\n{"objects": []}\n``` hope that helps')
    obs = analyze_view(model, view("top"))
    assert obs.data == {"objects": []}
    assert obs.error is None


def test_analyze_view_captures_model_error_instead_of_raising():
    def boom(system, user, image):
        raise RuntimeError("rate limited")

    obs = analyze_view(FakeModel(boom), view("left"))
    assert obs.data == {}
    assert obs.error is not None and "rate limited" in obs.error


def test_analyze_view_unparseable_keeps_raw_text_no_error():
    obs = analyze_view(FakeModel("no json here at all"), view("right"))
    assert obs.data == {}
    assert obs.error is None
    assert obs.raw_text == "no json here at all"


def test_run_workers_preserves_view_order():
    model = FakeModel(lambda s, u, img: '{"seen": true}')
    views = [view(f"v{i}") for i in range(5)]
    obs = run_workers(model, views)
    assert [o.label for o in obs] == ["v0", "v1", "v2", "v3", "v4"]


def test_run_workers_round_robins_over_the_key_pool():
    # Each model tags its reply with its own name, so the observation records
    # which pool member handled that view.
    pool = [FakeModel(f'{{"by": "{n}"}}', name=n) for n in ("k0", "k1", "k2")]
    views = [view(f"v{i}") for i in range(7)]
    obs = run_workers(pool, views, max_workers=4)
    handled_by = [o.data["by"] for o in obs]
    # view i -> pool[i % 3]
    assert handled_by == ["k0", "k1", "k2", "k0", "k1", "k2", "k0"]


def test_run_workers_empty_views_returns_empty():
    assert run_workers(FakeModel("{}"), []) == []


def test_run_workers_requires_a_model():
    import pytest

    with pytest.raises(ValueError):
        run_workers([], [view("v0")])
