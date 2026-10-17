"""The core's read-only web perception: boundary, bounded fetch, page sight.

No network: a fake opener plays the far end, exactly as the TS sandbox tests
inject a fake ``fetch``.
"""

from __future__ import annotations

import hashlib

import pytest
from secdogie_agent.browse import (
    PolicyError,
    bounded_fetch,
    canonical_origin,
    make_policy,
    scan_page,
)


def _adapter_ok() -> bool:
    try:
        import secdogie_graph

        secdogie_graph.library_path()
        return True
    except Exception:  # noqa: BLE001 - not importable or not built
        return False


# scan_page uses the native route adapter; the pure fetch/boundary tests do not.
needs_adapter = pytest.mark.skipif(not _adapter_ok(), reason="native route adapter not built")


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self._headers = {k.lower(): v for k, v in headers.items()}
        self._body = body

    def header(self, name: str) -> str | None:
        return self._headers.get(name.lower())

    def read_capped(self, max_bytes: int) -> tuple[bytes, bool]:
        if len(self._body) > max_bytes:
            return (self._body[:max_bytes], True)
        return (self._body, False)


def opener_for(responses: dict[str, FakeResponse]):
    seen: list[str] = []

    def opener(url: str, headers: dict[str, str], timeout_s: float) -> FakeResponse:
        seen.append(url)
        # the boundary forbids cookies/auth/referrer -- the request never carries them.
        assert "Cookie" not in headers and "Authorization" not in headers
        assert "Referer" not in headers and "referer" not in {k.lower() for k in headers}
        if url not in responses:
            raise ConnectionError("refused")
        return responses[url]

    opener.seen = seen  # type: ignore[attr-defined]
    return opener


HTML = {"content-type": "text/html; charset=utf-8"}


# ---- the boundary ---------------------------------------------------------

def test_canonical_origin_rejects_non_https_and_paths() -> None:
    assert canonical_origin("https://docs.example.com") == "https://docs.example.com"
    assert canonical_origin("https://docs.example.com:8443") == "https://docs.example.com:8443"
    for bad in ("http://docs.example.com", "https://u:p@docs.example.com", "https://docs.example.com/x"):
        with pytest.raises(PolicyError):
            canonical_origin(bad)


def test_empty_allowlist_is_refused_at_construction() -> None:
    with pytest.raises(PolicyError):
        make_policy([])


def test_admits_only_allowlisted_https_without_credentials() -> None:
    p = make_policy(["https://docs.example.com"])
    assert p.admits("https://docs.example.com/a#frag") == (True, "https://docs.example.com/a")
    assert p.admits("http://docs.example.com/a")[0] is False
    assert p.admits("https://other.example.com/a")[0] is False
    assert p.admits("https://u:pw@docs.example.com/a")[0] is False


# ---- bounded fetch --------------------------------------------------------

def test_refused_url_never_opens_a_connection() -> None:
    p = make_policy(["https://docs.example.com"])
    op = opener_for({})
    out = bounded_fetch(p, "https://evil.test/x", opener=op)
    assert out.kind == "refused"
    assert op.seen == []


def test_page_is_hashed_and_typed() -> None:
    p = make_policy(["https://docs.example.com"])
    body = b"<a href='/x'>x</a>"
    op = opener_for({"https://docs.example.com/": FakeResponse(200, HTML, body)})
    out = bounded_fetch(p, "https://docs.example.com/", opener=op)
    assert out.kind == "page"
    assert out.content_type == "text/html"
    assert out.content_hash == hashlib.sha256(body).hexdigest()
    assert out.truncated is False


def test_body_is_capped_and_marked_truncated() -> None:
    p = make_policy(["https://docs.example.com"], max_body_bytes=8)
    op = opener_for({"https://docs.example.com/": FakeResponse(200, HTML, b"<a>0123456789</a>")})
    out = bounded_fetch(p, "https://docs.example.com/", opener=op)
    assert out.kind == "page"
    assert out.byte_len == 8
    assert out.truncated is True


def test_redirect_is_reported_not_followed() -> None:
    p = make_policy(["https://docs.example.com"])
    op = opener_for(
        {"https://docs.example.com/old": FakeResponse(302, {"location": "/new"}, b"")}
    )
    out = bounded_fetch(p, "https://docs.example.com/old", opener=op)
    assert out.kind == "redirect"
    assert out.location == "https://docs.example.com/new"
    # only the one URL was ever opened -- the redirect was not chased.
    assert op.seen == ["https://docs.example.com/old"]


def test_offsite_redirect_target_is_dropped() -> None:
    p = make_policy(["https://docs.example.com"])
    op = opener_for(
        {"https://docs.example.com/old": FakeResponse(301, {"location": "https://evil.test/x"}, b"")}
    )
    out = bounded_fetch(p, "https://docs.example.com/old", opener=op)
    assert out.kind == "redirect"
    assert out.location is None


def test_non_html_is_failed() -> None:
    p = make_policy(["https://docs.example.com"])
    op = opener_for(
        {"https://docs.example.com/x": FakeResponse(200, {"content-type": "application/json"}, b"{}")}
    )
    out = bounded_fetch(p, "https://docs.example.com/x", opener=op)
    assert out.kind == "failed"
    assert "not HTML" in out.reason


# ---- perception (fetch + route scan) --------------------------------------

@needs_adapter
def test_scan_page_reports_only_allowlisted_candidates() -> None:
    p = make_policy(["https://docs.example.com"])
    body = b'<a href="/account/delete">x</a><a href="https://evil.test/y">y</a>'
    op = opener_for({"https://docs.example.com/": FakeResponse(200, HTML, body)})
    sight = scan_page(p, "https://docs.example.com/", opener=op)
    assert sight.is_page
    assert [c["route"] for c in sight.candidates] == ["/account/delete"]
    assert sight.scan is not None and sight.scan["external"] == 1


def test_scan_page_of_a_refusal_has_no_scan() -> None:
    p = make_policy(["https://docs.example.com"])
    sight = scan_page(p, "https://evil.test/x", opener=opener_for({}))
    assert not sight.is_page
    assert sight.scan is None
    assert sight.candidates == []
