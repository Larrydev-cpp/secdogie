"""End-to-end: the real secdogie_agent loop driving an AdbBackend, with only
adb (the OS boundary) and the model (the network boundary) faked. Proves the
Android backend plugs into the unmodified loop and that model coordinates on
the downscaled screenshot get scaled back to real device pixels before the tap.
"""
import io

from PIL import Image

from secdogie_agent.loop import AgentConfig, run
from secdogie_agent.providers.base import Action, VisionProvider

from secdogie_android.backend import AdbBackend
from tests.test_backend import FakeAdb


class ScriptedProvider(VisionProvider):
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def next_action(self, task, screenshot_png, screen_size, history):
        self.calls += 1
        return Action.from_dict(self.script.pop(0))


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def test_tap_is_scaled_from_model_space_to_device_pixels():
    # A tall 1080x2400 phone: long edge (2400) is capped to 1568, so the model
    # sees a ~706x1568 image. A tap the model returns at model-space (300, 700)
    # must be scaled up by 2400/1568 before it reaches adb.
    adb = FakeAdb(png=_png(1080, 2400))
    scale = 2400 / 1568
    provider = ScriptedProvider([
        {"action": "left_click", "x": 300, "y": 700},
        {"action": "done", "text": "tapped"},
    ])
    rc = run(provider, AgentConfig(task="tap", auto=True, max_steps=5, backend=AdbBackend(adb)))
    assert rc == 0
    kind, x, y = adb.calls[0]
    assert kind == "tap"
    assert (x, y) == (round(300 * scale), round(700 * scale))


def test_capture_failure_exits_cleanly():
    from secdogie_android.adb import AdbError

    adb = FakeAdb(screencap_error=AdbError("device offline"))
    provider = ScriptedProvider([{"action": "done", "text": "unreached"}])
    rc = run(provider, AgentConfig(task="x", auto=True, max_steps=3, backend=AdbBackend(adb)))
    assert rc == 4  # capture error -> clean exit, not a crash
    assert provider.calls == 0
