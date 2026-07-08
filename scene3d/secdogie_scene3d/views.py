"""A viewpoint is one image of the scene from one camera angle. The whole
premise of the multi-model pipeline is that the workers see *different* views
of the same 3D scene (front / top / left-45 / ...), so the aggregator can
recover depth and layout a single 2D frame can't show. Nine workers on nine
copies of one image would just be nine times the cost for the same blind spot.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Viewpoint:
    label: str            # short name for this angle, e.g. "front", "top", "left-45"
    image_png: bytes
    hint: str = ""        # optional camera description fed to the worker, e.g. "looking straight down"


def load_viewpoint(path: str, label: str | None = None, hint: str = "") -> Viewpoint:
    """Load an image file as a viewpoint; label defaults to the file stem."""
    data = Path(path).read_bytes()
    return Viewpoint(label=label or Path(path).stem, image_png=data, hint=hint)


def parse_view_arg(arg: str) -> tuple[str | None, str]:
    """Split a CLI view argument of the form `label=path` or plain `path`.
    Returns (label_or_None, path)."""
    if "=" in arg:
        label, _, path = arg.partition("=")
        return (label.strip() or None), path.strip()
    return None, arg
