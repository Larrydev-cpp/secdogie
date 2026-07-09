"""End-to-end HTTP tests: a real server on an ephemeral port, hit with real
requests (urllib, stdlib) -- proves routing, JSON (de)serialization, and
static file serving actually work together, not just Controller in isolation
(see test_controller.py for that)."""
import json
import threading
import urllib.error
import urllib.request

import pytest
from secdogie_open import controller as controller_mod
from secdogie_open import runner, windows
from secdogie_open.controller import Controller
from secdogie_open.server import build_server


def _window(id_="w1", title="App"):
    return windows.WindowInfo(id=id_, title=title, left=10, top=20, width=300, height=400)


def _resolved(api_key="sk-test"):
    return controller_mod.config_mod.ResolvedConfig(
        api_key=api_key, model="claude-sonnet-5", provider="anthropic", env_var="ANTHROPIC_API_KEY", api_key_source="test"
    )


@pytest.fixture
def live_server():
    server = build_server(Controller(), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    try:
        yield server, base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _get(base_url, path):
    resp = urllib.request.urlopen(base_url + path, timeout=2)
    return resp.status, resp.headers.get("Content-Type"), resp.read()


def _post_json(base_url, path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base_url + path, data=data, method="POST", headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=2)
    return resp.status, json.loads(resp.read())


# -- static files ---------------------------------------------------------

def test_index_served_at_root(live_server):
    _server, base_url = live_server
    status, content_type, body = _get(base_url, "/")
    assert status == 200
    assert "text/html" in content_type
    assert b"secdogie-open" in body


def test_style_and_script_served(live_server):
    _server, base_url = live_server
    status, content_type, body = _get(base_url, "/style.css")
    assert status == 200 and "text/css" in content_type and b"--accent" in body

    status, content_type, body = _get(base_url, "/app.js")
    assert status == 200 and "javascript" in content_type and b"fetch(" in body


def test_unknown_path_is_404(live_server):
    _server, base_url = live_server
    with pytest.raises(urllib.error.HTTPError) as ei:
        _get(base_url, "/nonexistent")
    assert ei.value.code == 404


# -- /api/windows ---------------------------------------------------------

def test_api_windows_returns_json_list(monkeypatch, live_server):
    win = _window()
    monkeypatch.setattr(controller_mod.windows, "list_windows", lambda: [win])
    _server, base_url = live_server

    status, _ct, body = _get(base_url, "/api/windows")
    assert status == 200
    data = json.loads(body)
    assert data == {"windows": [{"id": "w1", "title": "App", "left": 10, "top": 20, "width": 300, "height": 400}]}


def test_api_windows_surfaces_backend_error(monkeypatch, live_server):
    def boom():
        raise windows.NoWindowBackendError("no display available")

    monkeypatch.setattr(controller_mod.windows, "list_windows", boom)
    _server, base_url = live_server

    status, _ct, body = _get(base_url, "/api/windows")
    assert status == 200  # the error is reported in the JSON body, not an HTTP failure
    assert json.loads(body) == {"error": "no display available"}


# -- /api/thumbnail ---------------------------------------------------------

def test_api_thumbnail_unknown_id_is_404(live_server):
    _server, base_url = live_server
    with pytest.raises(urllib.error.HTTPError) as ei:
        _get(base_url, "/api/thumbnail?id=nope")
    assert ei.value.code == 404


def test_api_thumbnail_returns_png(monkeypatch, live_server):
    win = _window()
    monkeypatch.setattr(controller_mod.windows, "list_windows", lambda: [win])

    def fake_capture(region):
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (200, 100)).save(buf, format="PNG")
        return buf.getvalue(), (200, 100)

    monkeypatch.setattr(controller_mod.screen, "capture_screenshot", fake_capture)
    _server, base_url = live_server

    _get(base_url, "/api/windows")  # populate the controller's window cache
    status, content_type, body = _get(base_url, f"/api/thumbnail?id={win.id}")
    assert status == 200
    assert content_type == "image/png"
    assert body[:8] == b"\x89PNG\r\n\x1a\n"


# -- /api/status ---------------------------------------------------------

def test_api_status_empty_initially(live_server):
    _server, base_url = live_server
    status, _ct, body = _get(base_url, "/api/status")
    assert status == 200
    assert json.loads(body) == {}


# -- /api/models ---------------------------------------------------------

def test_api_models_returns_catalog(live_server):
    _server, base_url = live_server
    status, _ct, body = _get(base_url, "/api/models")
    assert status == 200
    data = json.loads(body)
    assert data["default"] == "claude-sonnet-5"
    assert any(p["id"] == "openai" for p in data["providers"])
    assert all(p["models"] and p["label"] for p in data["providers"])


# -- /api/start, /api/stop ---------------------------------------------------------

def test_api_start_validation_error_as_json(live_server):
    _server, base_url = live_server
    status, data = _post_json(base_url, "/api/start", {"task": "", "window_ids": ["w1"]})
    assert status == 200
    assert data["error"] == "Enter a task first."


def test_api_start_and_stop_round_trip(monkeypatch, live_server):
    win = _window()
    monkeypatch.setattr(controller_mod.windows, "list_windows", lambda: [win])
    monkeypatch.setattr(controller_mod.config_mod, "resolve", lambda **kw: _resolved())
    monkeypatch.setattr(controller_mod, "make_provider", lambda provider, model, key: "the-provider")
    monkeypatch.setattr(runner, "run", lambda provider, config: 0)
    _server, base_url = live_server

    _get(base_url, "/api/windows")  # populate the controller's window cache
    status, data = _post_json(
        base_url, "/api/start", {"task": "click it", "window_ids": [win.id], "auto": True, "max_steps": 5}
    )
    assert status == 200
    assert data == {"started": [win.id], "skipped": [], "error": None}

    status, data = _post_json(base_url, "/api/stop", {})
    assert status == 200
    assert data == {"stopped": []}  # already finished by the time we stop it (fake run() returns immediately)


def test_api_start_forwards_api_key_and_model_from_the_page(monkeypatch, live_server):
    win = _window()
    monkeypatch.setattr(controller_mod.windows, "list_windows", lambda: [win])
    seen = {}

    def fake_resolve(**kw):
        seen.update(kw)
        return _resolved()

    monkeypatch.setattr(controller_mod.config_mod, "resolve", fake_resolve)
    monkeypatch.setattr(controller_mod, "make_provider", lambda provider, model, key: "the-provider")
    monkeypatch.setattr(runner, "run", lambda provider, config: 0)
    _server, base_url = live_server

    _get(base_url, "/api/windows")  # populate the controller's window cache
    status, data = _post_json(
        base_url,
        "/api/start",
        {"task": "t", "window_ids": [win.id], "auto": True, "api_key": "sk-from-page", "model": "gpt-5.5"},
    )
    assert status == 200
    assert data["started"] == [win.id]
    assert seen["cli_api_key"] == "sk-from-page"
    assert seen["cli_model"] == "gpt-5.5"
