"""The fetch boundary of the core's read-only web perception.

A direct port of ``symbiont/src/sandbox/policy.ts``: fixed when a run starts and
never widened. HTTPS only, to an explicit allowlist of exact origins (empty
means nothing, never "anywhere"); no credentials of any kind (no cookies, no
auth, no userinfo in a URL, no referrer); a bounded body and a bounded time;
redirects are reported, never followed.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_MAX_BODY_BYTES = 2 << 20
HARD_MAX_BODY_BYTES = 8 << 20
DEFAULT_TIMEOUT_MS = 10_000
HARD_MAX_TIMEOUT_MS = 30_000
HTML_TYPES = ("text/html", "application/xhtml+xml")


class PolicyError(ValueError):
    """A malformed origin or an out-of-range bound."""


def _origin_of(parts) -> str:
    # URL.origin as the browser prints it: scheme://host[:port], lower-case
    # host, default port omitted.
    host = (parts.hostname or "").lower()
    port = parts.port
    default = {"https": 443, "http": 80}.get(parts.scheme)
    if port is None or port == default:
        return f"{parts.scheme}://{host}"
    return f"{parts.scheme}://{host}:{port}"


def canonical_origin(o: str) -> str:
    """``https://host[:port]`` exactly, or raise ``PolicyError``."""
    try:
        parts = urlsplit(o)
    except ValueError as e:
        raise PolicyError(f"not an origin: {o}") from e
    if parts.scheme != "https":
        raise PolicyError(f"only https origins are allowed: {o}")
    if parts.username or parts.password:
        raise PolicyError(f"an origin carries no credentials: {o}")
    canon = _origin_of(parts)
    # Reject anything with a path, query, fragment, or non-canonical spelling.
    if o != canon:
        raise PolicyError(f"write the origin exactly as {canon} (no path, lower-case host)")
    return canon


@dataclass(frozen=True)
class FetchPolicy:
    allowed_origins: tuple[str, ...]
    max_body_bytes: int
    timeout_ms: int

    def admits(self, raw: str) -> tuple[bool, str]:
        """``(True, url_without_fragment)`` or ``(False, reason)``."""
        try:
            parts = urlsplit(raw)
        except ValueError:
            return (False, "not a URL")
        if parts.scheme != "https":
            return (False, "not https")
        if parts.username or parts.password:
            return (False, "credentials in the URL")
        if _origin_of(parts) not in self.allowed_origins:
            return (False, "origin not on the allowlist")
        # drop the fragment
        rebuilt = parts._replace(fragment="").geturl()
        return (True, rebuilt)


def make_policy(
    allowed_origins: list[str] | tuple[str, ...],
    *,
    max_body_bytes: int | None = None,
    timeout_ms: int | None = None,
) -> FetchPolicy:
    origins = tuple(dict.fromkeys(canonical_origin(o) for o in allowed_origins))
    if not origins:
        raise PolicyError(
            "no allowed origins: the core fetches nothing without an explicit allowlist"
        )
    mbb = DEFAULT_MAX_BODY_BYTES if max_body_bytes is None else max_body_bytes
    tmo = DEFAULT_TIMEOUT_MS if timeout_ms is None else timeout_ms
    if not 0 < mbb <= HARD_MAX_BODY_BYTES:
        raise PolicyError(f"max_body_bytes must be in 1..{HARD_MAX_BODY_BYTES}")
    if not 0 < tmo <= HARD_MAX_TIMEOUT_MS:
        raise PolicyError(f"timeout_ms must be in 1..{HARD_MAX_TIMEOUT_MS}")
    return FetchPolicy(allowed_origins=origins, max_body_bytes=mbb, timeout_ms=tmo)
