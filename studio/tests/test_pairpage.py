"""The pairing page Studio serves on your network is the same one the browser build deploys.

Studio bundles ``pairpage/`` so it can host it with no outside service (local_pairing.py). Those files are
copies of ``webrtc/``; if the two ever drift, a phone pairing through Studio and a phone pairing through the
deployed page would run different code. This keeps them byte-for-byte identical.
"""
from __future__ import annotations

from pathlib import Path

import pytest

BUNDLED = Path(__file__).resolve().parents[1] / "secdogie_studio" / "pairpage"
SOURCE = Path(__file__).resolve().parents[2] / "webrtc"
FILES = ["pair.html", "pair.js", "ink.css", "client/frames.js", "client/keystore.js", "client/mesh.js",
         "client/meshlink.js", "client/pairing.js", "client/web_peer.js"]


@pytest.mark.skipif(not SOURCE.exists(), reason="the webrtc source tree is not in this checkout")
@pytest.mark.parametrize("name", FILES)
def test_the_bundled_pairing_page_matches_the_browser_source(name):
    assert (BUNDLED / name).read_bytes() == (SOURCE / name).read_bytes(), (
        f"studio/secdogie_studio/pairpage/{name} has drifted from webrtc/{name}; copy it over again")


def test_the_bundle_has_exactly_the_files_the_page_needs():
    present = {p.relative_to(BUNDLED).as_posix() for p in BUNDLED.rglob("*") if p.is_file()}  # as_posix: same on Windows
    assert present == set(FILES)
