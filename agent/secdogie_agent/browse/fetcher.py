"""One bounded, credential-free, non-following GET.

A port of ``symbiont/src/sandbox/bounded_fetch.ts``. The real transport is an
injectable *opener* so tests drive it without a network; the default opener uses
``http.client`` directly for full manual control -- no cookies, no redirect
following, exactly what goes on the wire and nothing else.
"""

from __future__ import annotations

import hashlib
import http.client
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from .policy import HTML_TYPES, FetchPolicy

_ACCEPT = "text/html, application/xhtml+xml;q=0.9"


@dataclass(frozen=True)
class FetchOutcome:
    kind: str  # "page" | "redirect" | "refused" | "failed"
    url: str
    status: int = 0
    content_type: str = ""
    body: bytes = b""
    byte_len: int = 0
    content_hash: str = ""
    truncated: bool = False
    location: str | None = None
    reason: str = ""


class RawResponse(Protocol):
    status: int

    def header(self, name: str) -> str | None: ...

    def read_capped(self, max_bytes: int) -> tuple[bytes, bool]: ...


# opener(url, headers, timeout_s) -> RawResponse, or raises on network failure.
Opener = Callable[[str, dict[str, str], float], RawResponse]


class _ClientResponse:
    def __init__(self, conn: http.client.HTTPConnection, resp: http.client.HTTPResponse) -> None:
        self._conn = conn
        self._resp = resp
        self.status = resp.status

    def header(self, name: str) -> str | None:
        return self._resp.getheader(name)

    def read_capped(self, max_bytes: int) -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        total = 0
        truncated = False
        try:
            while total <= max_bytes:
                chunk = self._resp.read(min(65536, max_bytes - total + 1))
                if not chunk:
                    break
                room = max_bytes - total
                if len(chunk) > room:
                    chunks.append(chunk[:room])
                    truncated = True
                    break
                chunks.append(chunk)
                total += len(chunk)
        finally:
            self._conn.close()
        return (b"".join(chunks), truncated)


def _https_opener(url: str, headers: dict[str, str], timeout_s: float) -> RawResponse:
    parts = urlsplit(url)
    conn = http.client.HTTPSConnection(parts.hostname or "", parts.port or 443, timeout=timeout_s)
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    conn.request("GET", target, headers=headers)
    return _ClientResponse(conn, conn.getresponse())


def bounded_fetch(policy: FetchPolicy, raw: str, *, opener: Opener = _https_opener) -> FetchOutcome:
    ok, admitted = policy.admits(raw)
    if not ok:
        return FetchOutcome(kind="refused", url=raw, reason=admitted)
    url = admitted
    headers = {"Accept": _ACCEPT, "Connection": "close"}
    try:
        res = opener(url, headers, policy.timeout_ms / 1000.0)
    except TimeoutError:
        return FetchOutcome(kind="failed", url=url, reason="timed out")
    except OSError as e:
        return FetchOutcome(kind="failed", url=url, reason=f"network refusal ({type(e).__name__})")

    status = res.status
    if 300 <= status < 400:
        loc = res.header("location")
        location: str | None = None
        if loc:
            ok2, admitted2 = policy.admits(urljoin(url, loc))
            location = admitted2 if ok2 else None
        return FetchOutcome(kind="redirect", url=url, status=status, location=location)
    if not 200 <= status < 300:
        return FetchOutcome(kind="failed", url=url, status=status, reason=f"HTTP {status}")

    content_type = (res.header("content-type") or "").split(";")[0].strip().lower()
    if content_type not in HTML_TYPES:
        return FetchOutcome(
            kind="failed", url=url, reason=f"not HTML ({content_type or 'no content-type'})"
        )
    try:
        body, truncated = res.read_capped(policy.max_body_bytes)
    except TimeoutError:
        return FetchOutcome(kind="failed", url=url, reason="timed out")
    return FetchOutcome(
        kind="page",
        url=url,
        status=status,
        content_type=content_type,
        body=body,
        byte_len=len(body),
        content_hash=hashlib.sha256(body).hexdigest(),
        truncated=truncated,
    )
