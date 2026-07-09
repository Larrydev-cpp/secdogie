"""End-to-end: the real secdogie_agent loop driving an IosBackend, with only
WDA (the network boundary) and the model faked. Proves the iOS backend plugs
into the unmodified loop and that model coordinates on the downscaled pixel
screenshot get scaled back to pixels by the loop, then down to WDA points by
the backend -- the full two-step coordinate path.
"""
from secdogie_agent.loop import AgentConfig, run
from secdogie_agent.providers.base import Action, VisionProvider
from secdogie_ios.backend import IosBackend
from tests.test_backend import FakeWda


class ScriptedProvider(VisionProvider):
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def next_action(self, task, screenshot_png, screen_size, history):
        self.calls += 1
        return Action.from_dict(self.script.pop(0))


def test_tap_scaled_from_model_space_through_pixels_to_points():
    # 1170x2532 pixel screen (3x, so 390x844 points). Long edge 2532 caps to
    # 1568, so the model sees a ~725x1568 image. It returns a tap; the loop
    # scales model->pixels, then IosBackend scales pixels->points.
    wda = FakeWda(pixel=(1170, 2532), point=(390, 844))
    provider = ScriptedProvider([
        {"action": "left_click", "x": 300, "y": 700},
        {"action": "done", "text": "tapped"},
    ])
    rc = run(provider, AgentConfig(task="tap", auto=True, max_steps=5, backend=IosBackend(wda)))
    assert rc == 0

    pixel_scale = 2532 / 1568   # model -> pixels (loop)
    px_per_pt = 1170 / 390      # pixels -> points (backend) = 3.0
    expect = (
        round(round(300 * pixel_scale) / px_per_pt),
        round(round(700 * pixel_scale) / px_per_pt),
    )
    assert wda.calls[0][0] == "tap"
    assert wda.calls[0][1:] == expect


def test_capture_failure_exits_cleanly():
    from secdogie_ios.wda import WdaError

    wda = FakeWda(screenshot_error=WdaError("device locked"))
    provider = ScriptedProvider([{"action": "done", "text": "unreached"}])
    rc = run(provider, AgentConfig(task="x", auto=True, max_steps=3, backend=IosBackend(wda)))
    assert rc == 4
    assert provider.calls == 0
