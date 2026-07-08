import threading

from secdogie_scene3d.views import Viewpoint


class FakeModel:
    """Records every describe() call and returns a canned reply. `reply` may be
    a string or a callable(system, user, image_png) -> string."""

    def __init__(self, reply, name="m"):
        self.reply = reply
        self.name = name
        self.calls = []
        self._lock = threading.Lock()

    def describe(self, system, user, image_png=None):
        with self._lock:
            self.calls.append((system, user, image_png))
        return self.reply(system, user, image_png) if callable(self.reply) else self.reply


def view(label, data=b"png"):
    return Viewpoint(label=label, image_png=data)
