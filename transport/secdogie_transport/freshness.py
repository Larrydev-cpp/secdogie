"""Signed-timestamp freshness for direct frames (P2P.2).

Every direct UDP frame (v1 signed plaintext and v2 sealed) carries `ts`, the
sender's wall clock in integer milliseconds, INSIDE the DID-signed envelope -- so
it cannot be altered without breaking the signature. A receiver accepts a frame
only if `ts` is within `max_skew` seconds of its own clock, in either direction.

This bounds how long a captured frame stays usable: a frame older than the skew
is dropped outright, and within the skew the per-peer counter window (sealed.py
`ReplayWindow`, applied to v1 and v2 alike) drops duplicates. Together they keep
hole-punch PROBE / PROBE-ACK traffic from being replayed to fake a direct path.

A missing, non-integer or boolean `ts` fails closed. Clocks are expected to be
NTP-synced; `max_skew` is the tolerance, not a security boundary on its own.
"""
from __future__ import annotations

import time

# Default tolerance between sender and receiver clocks, in seconds.
DEFAULT_MAX_SKEW = 30.0


def now_ms(clock=time.time) -> int:
    """`clock()` (seconds) as integer milliseconds -- the on-wire `ts` unit."""
    return int(clock() * 1000)


def is_fresh(ts, *, now: float, max_skew: float = DEFAULT_MAX_SKEW) -> bool:
    """Whether a frame timestamp `ts` (ms) is within `max_skew` seconds of `now`
    (seconds). Anything that is not a plain int fails closed."""
    if not isinstance(ts, int) or isinstance(ts, bool):
        return False
    return abs(now * 1000 - ts) <= max_skew * 1000


__all__ = ["DEFAULT_MAX_SKEW", "now_ms", "is_fresh"]
