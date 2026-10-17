"""The ctypes binding reaches the real Rust route adapter.

Skips as a whole where the native cdylib is not built (`cargo build --release`
in `graph/`); CI builds it so these parity checks actually run.
"""

from __future__ import annotations

import pytest

secdogie_graph = pytest.importorskip("secdogie_graph")
try:
    secdogie_graph.library_path()
except Exception as e:  # noqa: BLE001 - any load failure means "not built here"
    pytest.skip(f"native route adapter unavailable: {e}", allow_module_level=True)

from secdogie_graph import RouteAdapterError, canonical, route_scan  # noqa: E402


def test_scans_anchor_on_allowed_origin() -> None:
    scan = route_scan(
        "https://docs.example.com/",
        b'<a href="/account/delete">delete</a><a href="https://evil.test/x">x</a>',
        allowed_origins=["https://docs.example.com"],
    )
    keys = [c["route"] for c in scan["candidates"]]
    assert keys == ["/account/delete"]
    # the off-origin anchor is counted, never reported as a candidate.
    assert scan["external"] == 1
    assert scan["skipped_password_forms"] == 0


def test_password_form_is_skipped_whole() -> None:
    scan = route_scan(
        "https://docs.example.com/",
        b'<form action="/login" method="post"><input name="user"><input type="password" name="pw"></form>',
        allowed_origins=["https://docs.example.com"],
    )
    assert scan["candidates"] == []
    assert scan["skipped_password_forms"] == 1


def test_empty_allowlist_is_refused() -> None:
    # An empty allowlist is a configuration error, not "fetch nothing silently":
    # the adapter reports it so the caller cannot accidentally scan against no
    # boundary at all.
    with pytest.raises(RouteAdapterError):
        route_scan("https://docs.example.com/", b'<a href="/a">a</a>', allowed_origins=[])


def test_canonical_matches_core() -> None:
    assert canonical("[1.0, 1e16]") == "[1.0,1e+16]"


def test_error_is_a_value_not_a_trap() -> None:
    # A malformed value comes back as {"ok": false, ...} and is raised cleanly,
    # not as a crash of the host process.
    with pytest.raises(RouteAdapterError):
        canonical("not json")
