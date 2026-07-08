"""A thin client for a running WebDriverAgent (WDA) HTTP server.

WDA is the standard on-device automation server for iOS (it's what Appium
drives). You build and launch it once with Xcode; it then exposes an HTTP API
for screenshots and input. This module wraps only the handful of endpoints the
agent backend needs. Endpoint paths and body fields are taken from WDA's route
definitions (WebDriverAgentLib/Commands/*.m): FBScreenshotCommands (screenshot),
FBElementCommands (tap/doubleTap/touchAndHold/dragfromtoforduration/keys),
FBCustomCommands (pressButton/homescreen), FBSessionCommands (session/url).

Coordinate note: WDA takes tap/drag coordinates in *points*, while its
screenshot is in *pixels*. The pixel->point conversion lives in backend.py,
which knows both sizes; this layer passes coordinates through untouched.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

# WDA routes without `.withoutSession` require a /session/<id> prefix; capture
# (screenshot, window/size), status, and homescreen work without one, so those
# go through _request and the input endpoints go through _session_request.


class WdaError(RuntimeError):
    """A WebDriverAgent request failed, timed out, or wasn't reachable."""


class Wda:
    def __init__(self, base_url: str = "http://127.0.0.1:8100", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session_id: str | None = None

    # -- transport ---------------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        """Send one HTTP request to WDA and return the parsed JSON object.
        Isolated so tests can stub the transport in one place."""
        url = self.base_url + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise WdaError(f"WDA {method} {path} -> HTTP {e.code}: {detail.strip()}") from e
        except urllib.error.URLError as e:
            raise WdaError(
                f"could not reach WebDriverAgent at {self.base_url} ({e.reason}). Is WDA running "
                "and port-forwarded? See ios/README.md."
            ) from e
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise WdaError(f"WDA {method} {path} returned non-JSON: {raw[:200]!r}") from e

    def _session_request(self, method: str, subpath: str, body: dict | None = None) -> dict:
        sid = self.ensure_session()
        return self._request(method, f"/session/{sid}{subpath}", body)

    # -- session ---------------------------------------------------------
    def ensure_session(self) -> str:
        """Create a WDA session (bound to whatever is on screen) if we don't
        have one yet. Input endpoints need a session; capture does not."""
        if self._session_id is not None:
            return self._session_id
        resp = self._request("POST", "/session", {"capabilities": {"alwaysMatch": {}}})
        # WDA has returned the id both top-level and under value across versions.
        sid = resp.get("sessionId") or (resp.get("value") or {}).get("sessionId")
        if not sid:
            raise WdaError(f"WDA did not return a sessionId: {resp!r}")
        self._session_id = sid
        return sid

    def status(self) -> dict:
        return self._request("GET", "/status")

    # -- capture ---------------------------------------------------------
    def screenshot_png(self) -> bytes:
        resp = self._request("GET", "/screenshot")
        b64 = resp.get("value")
        if not isinstance(b64, str) or not b64:
            raise WdaError("WDA /screenshot returned no image (is the screen on and unlocked?)")
        return base64.b64decode(b64)

    def window_size(self) -> tuple[int, int]:
        """Screen size in *points* (not pixels). Used to derive the pixel->point
        scale for mapping model coordinates onto WDA's coordinate space."""
        resp = self._request("GET", "/window/size")
        v = resp.get("value") or {}
        try:
            return int(v["width"]), int(v["height"])
        except (KeyError, TypeError, ValueError) as e:
            raise WdaError(f"WDA /window/size returned an unexpected shape: {resp!r}") from e

    # -- input (point coordinates) ---------------------------------------------------------
    def tap(self, x: int, y: int) -> None:
        self._session_request("POST", "/wda/tap", {"x": x, "y": y})

    def double_tap(self, x: int, y: int) -> None:
        self._session_request("POST", "/wda/doubleTap", {"x": x, "y": y})

    def touch_and_hold(self, x: int, y: int, duration: float = 1.0) -> None:
        self._session_request("POST", "/wda/touchAndHold", {"x": x, "y": y, "duration": duration})

    def drag(self, from_x: int, from_y: int, to_x: int, to_y: int, duration: float = 0.5) -> None:
        self._session_request(
            "POST",
            "/wda/dragfromtoforduration",
            {"fromX": from_x, "fromY": from_y, "toX": to_x, "toY": to_y, "duration": duration},
        )

    def type_text(self, text: str) -> None:
        # WDA types into the currently focused field; unlike adb it handles
        # Unicode. `value` is an array of strings that WDA concatenates.
        self._session_request("POST", "/wda/keys", {"value": [text]})

    def press_button(self, name: str) -> None:
        self._session_request("POST", "/wda/pressButton", {"name": name})

    def homescreen(self) -> None:
        self._request("POST", "/wda/homescreen")

    def open_url(self, url: str) -> None:
        self._session_request("POST", "/url", {"url": url})
