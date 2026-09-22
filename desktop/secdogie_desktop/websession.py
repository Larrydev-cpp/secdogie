"""Reuse an already-authorized browser session to read a page for the Agent.

The operator logs in themselves, through the site's normal flow, in a real
browser, and saves their OWN authorized session to a local file -- Playwright's
`storage_state.json`, or a persistent `user_data_dir` profile. This module only
*reuses* that saved session to navigate to a page and read its structure; it then
hands the accessibility tree + text to the Agent for analysis.

This is ordinary authorized automation, the documented purpose of Playwright's
`storage_state`. It is emphatically NOT a bypass:

  * Login happens out of band, by the operator; this module never types
    credentials, never creates or captures a session, and never defeats an
    authentication control.
  * The session file is the operator's own. It is read from a local path only
    and is never transmitted anywhere.
  * Read-only: it navigates and reads (accessibility snapshot + text). It does
    not fill forms or submit.
  * No stealth. It does not spoof fingerprints, evade bot-detection, or solve
    CAPTCHAs. If a site refuses automation, that refusal is respected, not
    defeated.
  * http/https only.

Playwright is an optional dependency, imported lazily inside `PlaywrightDriver`,
so importing this module (and its pure parsing) needs no browser. The browser
binary is the one already on the machine (`PLAYWRIGHT_BROWSERS_PATH`); nothing is
downloaded. The browser interaction sits behind a `BrowserDriver` seam so the
navigate/extract flow is unit-tested headlessly with a fake driver.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class AuthorizedContext:
    """Which already-authorized session to reuse. Exactly one of `storage_state`
    (a Playwright storage-state JSON file the operator saved) or `user_data_dir`
    (a persistent browser profile directory) must be given, and it must exist."""

    storage_state: str | None = None
    user_data_dir: str | None = None
    headless: bool = True

    def validate(self) -> AuthorizedContext:
        provided = [p for p in (self.storage_state, self.user_data_dir) if p]
        if len(provided) != 1:
            raise ValueError("provide exactly one of storage_state or user_data_dir")
        path = provided[0]
        if not os.path.exists(path):
            raise FileNotFoundError(f"authorized session path not found: {path}")
        return self


@dataclass(frozen=True)
class WebAxNode:
    """A node of the page's accessibility tree, normalized from Playwright's
    `accessibility.snapshot()` ({role, name, value, children})."""

    role: str = ""
    name: str = ""
    value: str = ""
    children: tuple[WebAxNode, ...] = ()


@dataclass(frozen=True)
class PageObservation:
    """What `read_page` hands to the Agent: the resolved URL, page title, visible
    text, and the normalized accessibility tree."""

    url: str
    title: str = ""
    text: str = ""
    ax_nodes: tuple[WebAxNode, ...] = ()


@dataclass
class RawPage:
    """What a `BrowserDriver` returns before normalization."""

    url: str
    title: str = ""
    ax_snapshot: dict | None = None
    text: str = ""


class BrowserDriver(Protocol):
    """The seam: open the authorized context, navigate to `url`, and return the
    raw page. Production is `PlaywrightDriver`; tests inject a fake."""

    def fetch(self, cfg: AuthorizedContext, url: str) -> RawPage: ...


def safe_url(url: str) -> bool:
    """Only real http/https URLs with a host are navigable -- rejects file:,
    javascript:, data:, about:, and empty/garbage."""
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    return parsed.scheme in _ALLOWED_SCHEMES and bool(parsed.netloc)


def normalize_ax_snapshot(snapshot: dict | None) -> list[WebAxNode]:
    """Turn Playwright's nested accessibility snapshot into WebAxNodes. A missing
    or non-dict snapshot yields []. The snapshot's root is itself a node."""
    if not isinstance(snapshot, dict):
        return []

    def build(node: dict) -> WebAxNode:
        raw_children = node.get("children")
        children = (
            tuple(build(c) for c in raw_children if isinstance(c, dict))
            if isinstance(raw_children, list)
            else ()
        )
        value = node.get("value")
        return WebAxNode(
            role=str(node.get("role", "")),
            name=str(node.get("name", "")),
            value="" if value is None else str(value),
            children=children,
        )

    return [build(snapshot)]


def flatten_nodes(nodes) -> list[WebAxNode]:
    """Depth-first flattening, so the Agent can scan every node without walking
    the tree itself."""
    out: list[WebAxNode] = []
    for n in nodes:
        out.append(n)
        out.extend(flatten_nodes(n.children))
    return out


def read_page(url: str, cfg: AuthorizedContext, *, driver: BrowserDriver | None = None) -> PageObservation:
    """Reuse the authorized session in `cfg` to navigate to `url` and return a
    `PageObservation` for the Agent. Validates the URL and context BEFORE any
    navigation, so a bad request never drives the browser."""
    if not safe_url(url):
        raise ValueError(f"refusing to navigate to a non-http(s) URL: {url!r}")
    cfg.validate()
    drv = driver if driver is not None else PlaywrightDriver()
    raw = drv.fetch(cfg, url)
    return PageObservation(
        url=raw.url or url,
        title=raw.title,
        text=raw.text,
        ax_nodes=tuple(normalize_ax_snapshot(raw.ax_snapshot)),
    )


class PlaywrightDriver:
    """The production driver. Builds a browser context from the operator's saved
    authorized session, navigates once, and reads the accessibility tree + text.
    Playwright is imported lazily so this module loads without it."""

    def __init__(self, *, wait_until: str = "domcontentloaded"):
        self.wait_until = wait_until

    def fetch(self, cfg: AuthorizedContext, url: str) -> RawPage:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            if cfg.user_data_dir:
                # A persistent profile IS the authorized session; no browser wrapper.
                context = p.chromium.launch_persistent_context(
                    cfg.user_data_dir, headless=cfg.headless
                )
                page = context.new_page()
                close = context.close
            else:
                browser = p.chromium.launch(headless=cfg.headless)
                context = browser.new_context(storage_state=cfg.storage_state)
                page = context.new_page()
                close = browser.close
            try:
                page.goto(url, wait_until=self.wait_until)
                title = page.title()
                snapshot = page.accessibility.snapshot()
                try:
                    text = page.inner_text("body")
                except Exception:
                    text = ""  # a page with no body still yields a snapshot/title
                return RawPage(url=page.url, title=title, ax_snapshot=snapshot, text=text)
            finally:
                close()


__all__ = [
    "AuthorizedContext",
    "WebAxNode",
    "PageObservation",
    "RawPage",
    "BrowserDriver",
    "PlaywrightDriver",
    "safe_url",
    "normalize_ax_snapshot",
    "flatten_nodes",
    "read_page",
]
