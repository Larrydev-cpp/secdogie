"""Read-only web perception: fetch one page, then report the candidate states
its markup names -- the headless equivalent of a screenshot for the no-device
core. It never follows a link; turning a candidate into an observed state is a
separate, bounded fetch the planning loop decides on.

The route adapter (``secdogie_graph``) is imported lazily so the module loads
even where the native library is absent; a page that is fetched but cannot be
scanned surfaces ``RouteAdapterUnavailable`` rather than silently yielding
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .fetcher import FetchOutcome, Opener, _https_opener, bounded_fetch
from .policy import FetchPolicy


@dataclass(frozen=True)
class PageSight:
    """What the core saw at one URL: the fetch outcome, and -- for a page --
    the route scan (``document`` + ``candidates`` + counts). ``scan`` is ``None``
    for a refusal, redirect or failure."""

    outcome: FetchOutcome
    scan: dict[str, Any] | None = None

    @property
    def is_page(self) -> bool:
        return self.outcome.kind == "page"

    @property
    def candidates(self) -> list[dict[str, Any]]:
        return list(self.scan["candidates"]) if self.scan else []


def scan_page(policy: FetchPolicy, url: str, *, opener: Opener = _https_opener) -> PageSight:
    outcome = bounded_fetch(policy, url, opener=opener)
    if outcome.kind != "page":
        return PageSight(outcome=outcome)
    from secdogie_graph import route_scan  # lazy: optional native dependency

    scan = route_scan(
        outcome.url,
        outcome.body,
        allowed_origins=list(policy.allowed_origins),
    )
    return PageSight(outcome=outcome, scan=scan)
