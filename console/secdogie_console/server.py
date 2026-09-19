"""Local-only HTTP server exposing the secdogie-console controller + web UI.

Mirrors secdogie-open's server: a ThreadingHTTPServer bound to 127.0.0.1, a
handler that serves the static UI and a small JSON API. Reads are open over
loopback; mutating commands go through ConsoleController.authorize (operator-DID
signature when an allowlist is configured). For remote reach, front this with
the repo's cloudflared + Cloudflare Access setup rather than binding a public
port.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .controller import ConsoleController

_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def _webui_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "secdogie_console" / "webui"
    return Path(__file__).resolve().parent / "webui"


def make_handler(controller: ConsoleController) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "secdogie-console/1"

        def log_message(self, fmt: str, *args) -> None:
            pass

        def _send_json(self, obj, status: int = 200) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            obj = json.loads(raw or b"{}")
            if not isinstance(obj, dict):
                raise ValueError("body must be a JSON object")
            return obj

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in _STATIC_FILES:
                filename, content_type = _STATIC_FILES[path]
                self._send_bytes((_webui_dir() / filename).read_bytes(), content_type)
                return
            if path == "/api/state":
                self._send_json(controller.state_snapshot())
                return
            self.send_error(404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path != "/api/command":
                self.send_error(404)
                return
            try:
                body = self._read_json_body()
            except (json.JSONDecodeError, ValueError):
                self._send_json({"error": "invalid JSON body"}, status=400)
                return
            ok, signer = controller.authorize(body)
            if not ok:
                self._send_json(
                    {"error": "unauthorized: operator DID signature required", "signer": signer},
                    status=403,
                )
                return
            try:
                self._send_json(controller.command(body))
            except ValueError as e:
                self._send_json({"error": str(e)}, status=400)

    return Handler


def build_server(controller: ConsoleController, port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(controller))
