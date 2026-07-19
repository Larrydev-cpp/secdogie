"""Thin Ultralytics YOLO adapter implementing the Detector protocol.

YOLO is the local, frame-rate half of perception -- a cloud vision LLM cannot
sit in a 60 Hz loop (1-3 s per call), and the reflex layer's template matching
cannot track a 3D model that rotates and scales. A small fine-tuned YOLO (e.g.
yolov8n on a few hundred labeled dragon frames) runs per-frame on the GPU
machine and feeds controller.engage.

This adapter is deliberately just plumbing: model file in, Detection list out.
It can only be truly verified on a machine with a GPU + the game (see README's
"verify on the machine" section); everything downstream of the Detection list
is proven headless in tests/test_controller.py.
"""
from __future__ import annotations

import io

from .controller import Detection


def _require_ultralytics():
    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise RuntimeError(
            "the YOLO detector needs the ultralytics package. Install it with "
            "`pip install 'secdogie-aim[yolo]'` (or `pip install ultralytics`)."
        ) from e
    return YOLO


class YoloDetector:
    """Detector backed by an Ultralytics YOLO model (.pt weights)."""

    def __init__(self, weights: str, *, min_confidence: float = 0.25):
        YOLO = _require_ultralytics()
        self._model = YOLO(weights)
        self._min_confidence = min_confidence

    def detect(self, frame_png: bytes) -> list[Detection]:
        from PIL import Image

        with Image.open(io.BytesIO(frame_png)) as img:
            frame = img.convert("RGB")
            # verbose=False: this runs every frame; per-call console spam would
            # drown the terminal and cost more than the inference on small models.
            results = self._model.predict(frame, conf=self._min_confidence, verbose=False)

        out: list[Detection] = []
        for r in results:
            names = r.names  # class-id -> label mapping carried by the model
            for b in r.boxes:
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
                out.append(
                    Detection(
                        cx=(x1 + x2) / 2.0,
                        cy=(y1 + y2) / 2.0,
                        w=x2 - x1,
                        h=y2 - y1,
                        confidence=float(b.conf[0]),
                        label=str(names.get(int(b.cls[0]), "")),
                    )
                )
        return out
