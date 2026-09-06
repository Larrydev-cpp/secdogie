"""Local-only HTTP server exposing the secdogie-open controller and web UI."""
from __future__ import annotations

import dataclasses
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import windows
from .controller import Controller, model_catalog

_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def _webui_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "secdogie_open" / "webui"
    return Path(__file__).resolve().parent / "webui"


def _window_to_dict(win: windows.WindowInfo) -> dict:
    return {"id": win.id, "title": win.title, "left": win.left, "top": win.top, "width": win.width, "height": win.height}


def make_handler(controller: Controller) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "secdogie-open/1"

        def log_message(self, fmt: str, *args) -> None:
            pass

        def _send_json(self, obj, status: int = 200) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status); self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0")); raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw or b"{}")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in _STATIC_FILES:
                filename, content_type = _STATIC_FILES[parsed.path]; self._send_bytes((_webui_dir() / filename).read_bytes(), content_type); return
            if parsed.path == "/api/windows": self._handle_get_windows(); return
            if parsed.path == "/api/thumbnail": self._handle_get_thumbnail(parsed); return
            if parsed.path == "/api/status": self._send_json(controller.status_snapshot()); return
            if parsed.path == "/api/models": self._send_json(model_catalog()); return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/start": self._handle_post_start(); return
            if parsed.path == "/api/stop": self._send_json({"stopped": controller.stop_all()}); return
            self.send_error(404)

        def _handle_get_windows(self) -> None:
            try: found = controller.refresh_windows()
            except windows.NoWindowBackendError as e: self._send_json({"error": str(e)}); return
            self._send_json({"windows": [_window_to_dict(w) for w in found]})

        def _handle_get_thumbnail(self, parsed) -> None:
            window_id = parse_qs(parsed.query).get("id", [""])[0]
            png = controller.thumbnail_png(window_id) if window_id else None
            if png is None: self.send_error(404); return
            self._send_bytes(png, "image/png")

        def _handle_post_start(self) -> None:
            try: body = self._read_json_body()
            except json.JSONDecodeError: self._send_json({"error": "invalid JSON body"}, status=400); return
            try:
                result = controller.start(
                    window_ids=body.get("window_ids") or [], task=body.get("task") or "",
                    model=body.get("model") or "", models=body.get("models") or None,
                    max_steps=int(body.get("max_steps") or 50), auto=bool(body.get("auto")), api_key=body.get("api_key") or "",
                )
            except (TypeError, ValueError) as e:
                self._send_json({"error": f"invalid start request: {e}"}, status=400); return
            self._send_json(dataclasses.asdict(result))

    return Handler


def build_server(controller: Controller, port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(controller))
