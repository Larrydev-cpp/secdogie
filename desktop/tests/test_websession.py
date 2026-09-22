"""Tests for reusing an already-authorized browser session (desktop/websession).

Headless: a FakeDriver stands in for the browser so the validate -> navigate ->
extract -> normalize flow is exercised with no Playwright and no network. A real-
browser smoke test is guarded by importorskip and skips where Playwright isn't
installed."""
from __future__ import annotations

import pytest
from secdogie_desktop.websession import (
    AuthorizedContext,
    PageObservation,
    RawPage,
    flatten_nodes,
    normalize_ax_snapshot,
    read_page,
    safe_url,
)


class FakeDriver:
    """Records what it was asked to fetch and returns a canned page. Never opens
    a real browser -- the whole point of the driver seam."""

    def __init__(self, raw: RawPage):
        self._raw = raw
        self.calls: list[tuple[AuthorizedContext, str]] = []

    def fetch(self, cfg, url):
        self.calls.append((cfg, url))
        return self._raw


# --- AuthorizedContext.validate ---------------------------------------------


def test_validate_accepts_exactly_one_existing_path(tmp_path):
    ss = tmp_path / "storage_state.json"
    ss.write_text("{}")
    assert AuthorizedContext(storage_state=str(ss)).validate().storage_state == str(ss)
    profile = tmp_path / "profile"
    profile.mkdir()
    assert AuthorizedContext(user_data_dir=str(profile)).validate().user_data_dir == str(profile)


def test_validate_rejects_neither_or_both(tmp_path):
    ss = tmp_path / "s.json"
    ss.write_text("{}")
    with pytest.raises(ValueError):
        AuthorizedContext().validate()  # neither
    with pytest.raises(ValueError):
        AuthorizedContext(storage_state=str(ss), user_data_dir=str(tmp_path)).validate()  # both


def test_validate_rejects_missing_path():
    with pytest.raises(FileNotFoundError):
        AuthorizedContext(storage_state="/no/such/state.json").validate()


# --- safe_url ---------------------------------------------------------------


def test_safe_url_allows_only_http_https():
    assert safe_url("https://example.com/page")
    assert safe_url("http://10.0.0.5:8080/x")
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", "about:blank", "", "ftp://h/x"):
        assert not safe_url(bad), bad


# --- normalize_ax_snapshot --------------------------------------------------


def test_normalize_flattens_playwright_snapshot():
    snapshot = {
        "role": "WebArea",
        "name": "Example",
        "children": [
            {"role": "heading", "name": "Title"},
            {"role": "link", "name": "Home", "value": "/", "children": [{"role": "text", "name": "Home"}]},
            "not-a-node",  # ignored
        ],
    }
    roots = normalize_ax_snapshot(snapshot)
    assert len(roots) == 1
    root = roots[0]
    assert root.role == "WebArea" and root.name == "Example"
    assert [c.role for c in root.children] == ["heading", "link"]  # the string child was dropped
    link = root.children[1]
    assert link.value == "/" and link.children[0].name == "Home"
    # flatten_nodes walks the whole tree
    roles = [n.role for n in flatten_nodes(roots)]
    assert roles == ["WebArea", "heading", "link", "text"]


def test_normalize_handles_none_and_non_dict():
    assert normalize_ax_snapshot(None) == []
    assert normalize_ax_snapshot("nope") == []  # type: ignore[arg-type]


# --- read_page (the flow) ---------------------------------------------------


def _ctx(tmp_path):
    ss = tmp_path / "storage_state.json"
    ss.write_text("{}")
    return AuthorizedContext(storage_state=str(ss))


def test_read_page_navigates_and_extracts(tmp_path):
    cfg = _ctx(tmp_path)
    raw = RawPage(
        url="https://example.com/final",  # e.g. after a redirect
        title="Example",
        ax_snapshot={"role": "WebArea", "name": "Example", "children": [{"role": "button", "name": "Go"}]},
        text="Hello world",
    )
    driver = FakeDriver(raw)
    obs = read_page("https://example.com/start", cfg, driver=driver)
    assert isinstance(obs, PageObservation)
    # the driver was handed the exact cfg + url; navigation happened once
    assert driver.calls == [(cfg, "https://example.com/start")]
    assert obs.url == "https://example.com/final"  # resolved URL wins
    assert obs.title == "Example" and obs.text == "Hello world"
    assert [n.role for n in flatten_nodes(obs.ax_nodes)] == ["WebArea", "button"]


def test_read_page_passes_the_right_context_kind(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    cfg = AuthorizedContext(user_data_dir=str(profile))
    driver = FakeDriver(RawPage(url="https://x.test/"))
    read_page("https://x.test/", cfg, driver=driver)
    got_cfg, _ = driver.calls[0]
    assert got_cfg.user_data_dir == str(profile) and got_cfg.storage_state is None


def test_read_page_rejects_bad_url_before_navigating(tmp_path):
    cfg = _ctx(tmp_path)
    driver = FakeDriver(RawPage(url="x"))
    with pytest.raises(ValueError):
        read_page("file:///etc/passwd", cfg, driver=driver)
    assert driver.calls == []  # never touched the browser


def test_read_page_validates_context_before_navigating():
    driver = FakeDriver(RawPage(url="x"))
    with pytest.raises(FileNotFoundError):
        read_page("https://example.com", AuthorizedContext(storage_state="/missing.json"), driver=driver)
    assert driver.calls == []


# --- real browser (skips without Playwright installed) ----------------------


def test_real_browser_smoke_reads_a_data_document(tmp_path):
    pytest.importorskip("playwright")
    from secdogie_desktop.websession import PlaywrightDriver

    ss = tmp_path / "storage_state.json"
    ss.write_text('{"cookies": [], "origins": []}')  # a valid empty authorized state
    cfg = AuthorizedContext(storage_state=str(ss))
    # A data document needs no login; it just proves the driver path works. The
    # driver only accepts http/https via read_page, so drive PlaywrightDriver
    # directly here with a data URL for the smoke.
    raw = PlaywrightDriver().fetch(cfg, "data:text/html,<button>Go</button><p>hi</p>")
    assert raw.ax_snapshot is not None
