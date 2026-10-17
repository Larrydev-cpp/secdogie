"""Read-only web perception for the no-device core.

The core, running headless on a host the operator controls, can browse public
content and think without any local actuator: ``bounded_fetch`` gets one page
within a fixed boundary (HTTPS only, allowlisted origins, no credentials,
bounded body/time, redirects reported not followed), and ``scan_page`` reports
the candidate states the page names via the shared Rust route adapter. Nothing
here changes external state; actions that would are suspended at Gate 2.
"""

from __future__ import annotations

from .fetcher import FetchOutcome, bounded_fetch
from .perception import PageSight, scan_page
from .policy import (
    DEFAULT_MAX_BODY_BYTES,
    DEFAULT_TIMEOUT_MS,
    HARD_MAX_BODY_BYTES,
    HARD_MAX_TIMEOUT_MS,
    FetchPolicy,
    PolicyError,
    canonical_origin,
    make_policy,
)

__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_TIMEOUT_MS",
    "HARD_MAX_BODY_BYTES",
    "HARD_MAX_TIMEOUT_MS",
    "FetchOutcome",
    "FetchPolicy",
    "PageSight",
    "PolicyError",
    "bounded_fetch",
    "canonical_origin",
    "make_policy",
    "scan_page",
]
