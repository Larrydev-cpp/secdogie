"""End to end: a real browser (the pre-installed Playwright Chromium) joins a
Python mesh as an admitted, forward-only node over WebRTC, and carries an
inference request.

The serving node S and the model node P have no link of their own: the only
path is S -> B -> P through the browser node B (symbiont/mesh.html). The
operator admits B's DID; B presents the admission in its rtc-hello, S and P
publish it, and from then on B's heartbeats count -- with its role claims cut
to forward. A user's request is answered through B; closing the tab removes B
from every route.

Skipped unless aiortc, Node, the symbiont toolchain (npm ci) and Playwright
are available.
"""
from __future__ import annotations

import functools
import http.server
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("nacl")
pytest.importorskip("aiortc")
pytest.importorskip("websockets")

from nacl.public import PrivateKey  # noqa: E402
from secdogie_identity import Allowlist, Identity, create_binding  # noqa: E402
from secdogie_inference import (  # noqa: E402
    OUTCOME_ACCEPTED,
    AdmissionBook,
    Config,
    KeyBroker,
    MemoryKeySource,
    MeshNode,
    TrustRoots,
    UserClient,
    assignments_from_bindings,
    make_admission,
)
from secdogie_inference.protocol import ROLE_FORWARD, ROLE_MODEL, ROLE_SERVE, AnyOf  # noqa: E402
from secdogie_inference.rtc import RtcWire  # noqa: E402
from signaling_stub import SignalingStub  # noqa: E402
from sim import echo, public_key_b64  # noqa: E402

SYMBIONT = Path(__file__).resolve().parents[2] / "symbiont"
NODE = shutil.which("node")


def _toolchain() -> str | None:
    if NODE is None:
        return "node is not installed"
    if not (SYMBIONT / "node_modules" / "typescript").is_dir():
        return "symbiont dependencies are not installed (npm ci)"
    probe = subprocess.run([NODE, "-e", "require('playwright')"], capture_output=True, cwd=SYMBIONT)
    if probe.returncode != 0:
        return "playwright is not available to node"
    return None


pytestmark = pytest.mark.skipif(_toolchain() is not None, reason=_toolchain() or "")


def wait(cond, timeout=30.0, what="condition"):
    deadline = time.time() + timeout
    while not cond():
        assert time.time() < deadline, f"{what} never held"
        time.sleep(0.1)


@pytest.fixture(scope="module")
def page_url():
    """Build symbiont's dist/ and serve the package directory over HTTP (127.0.0.1
    is a secure context, so WebCrypto and IndexedDB work)."""
    tsc = SYMBIONT / "node_modules" / "typescript" / "bin" / "tsc"
    subprocess.run([NODE, str(tsc), "-p", "tsconfig.build.json"], cwd=SYMBIONT, check=True, timeout=300)
    handler = functools.partial(_QuietHandler, directory=str(SYMBIONT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/mesh.html"
    server.shutdown()
    server.server_close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Driver:
    """The Playwright driver (symbiont/tests/e2e/mesh_driver.mjs), one JSON line each way."""

    def __init__(self, url: str):
        self.proc = subprocess.Popen([NODE, str(SYMBIONT / "tests" / "e2e" / "mesh_driver.mjs"), url],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=SYMBIONT)

    def read(self, timeout=120.0) -> dict:
        box: list = []
        t = threading.Thread(target=lambda: box.append(self.proc.stdout.readline()), daemon=True)
        t.start()
        t.join(timeout)
        assert box and box[0], "the browser driver said nothing (see its stderr above)"
        return json.loads(box[0])

    def write(self, obj: dict) -> None:
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(10)


def test_a_browser_node_carries_a_request_and_leaves_no_route_behind(page_url):
    ids = {n: Identity.generate() for n in ("U", "S", "P", "K", "OP")}
    did = {n: i.did for n, i in ids.items()}
    static = {did["S"], did["P"], did["K"]}
    lists = {"users": {did["U"]}, "deciders": {did["S"]}, "coordinators": set(), "brokers": {did["K"]},
             "operators": {did["OP"]}}
    books = {n: AdmissionBook() for n in ("S", "P", "K", "U")}

    def roots(n):
        return TrustRoots(mesh=AnyOf(Allowlist(set(static)), books[n]),
                          **{k: Allowlist(set(v)) for k, v in lists.items()})

    live = Config(heartbeat_every=0.5, fail_after=4.0, leg_timeout=4.0, advise_every=1.0)
    signaling = SignalingStub()
    parts: dict = {}
    wires = {n: RtcWire(ids[n], roots(n), signaling.url, listen=n in ("S", "P", "K"),
                        on_admission=lambda adm, n=n: parts[n].publish(adm))
             for n in ("S", "P", "K", "U")}
    tkey = PrivateKey.generate()
    binding = create_binding(ids["P"], public_key_b64(tkey), key_version=1)
    parts["S"] = MeshNode(ids["S"], wires["S"], roots("S"), roles={ROLE_SERVE, ROLE_FORWARD}, config=live)
    parts["P"] = MeshNode(ids["P"], wires["P"], roots("P"), roles={ROLE_MODEL}, backend=echo, transport_key=tkey,
                          config=live)
    parts["K"] = KeyBroker(ids["K"], wires["K"], roots("K"), MemoryKeySource({"m/a": "sk-browser"}),
                           assignments=assignments_from_bindings([binding], {did["P"]: ["m/a"]}, roots("K")),
                           config=live)
    parts["U"] = UserClient(ids["U"], wires["U"], roots("U"), entries=[did["S"]], config=live, timeout=10.0)
    for n, part in parts.items():
        if n != "U":
            part.attach_admissions(books[n])
        wires[n].attach(part)
    parts["P"].connect(did["K"])  # S and P are not linked: only the browser joins them

    stop = threading.Event()

    def ticker():
        while not stop.is_set():
            for part in parts.values():
                part.tick()
            time.sleep(0.05)

    threading.Thread(target=ticker, daemon=True).start()
    driver = Driver(page_url)
    try:
        browser = driver.read()["did"]
        assert browser.startswith("did:key:") and browser not in static
        bundle = {"type": "secdogie/inference/join/v1", "signaling": signaling.url, "salt": "",
                  "entries": [did["S"], did["P"]], "heartbeat_every": 0.5,
                  "trust": {"mesh": sorted(static), **{k: sorted(v) for k, v in lists.items()}},
                  "admission": make_admission(ids["OP"], browser, ttl=3600)}
        driver.write({"bundle": json.dumps(bundle)})
        assert sorted(driver.read()["joined"]) == sorted([did["S"], did["P"]])

        s = parts["S"]
        wait(lambda: s.trust.mesh.contains(browser), what="S admitting the browser")
        wait(lambda: s.lsdb.roles(browser) == {ROLE_FORWARD}, what="the browser's heartbeat at S")
        wait(lambda: browser in s.lsdb.adjacency(time.time()).get(did["P"], set())
             and browser in s.lsdb.adjacency(time.time()).get(did["S"], set()),
             timeout=30, what="witnessed links S - B - P")
        wait(lambda: s.leases.eligible(["m/a"], time.time()), timeout=40, what="P's lease reaching S through B")

        handle = parts["U"].ask(["m/a"], "through the browser")
        wait(lambda: parts["U"].outcome(handle) is not None, timeout=30, what="the outcome")
        out = parts["U"].outcome(handle)
        assert out.status == OUTCOME_ACCEPTED and out.output == "answer(through the browser)"
        # The route: S never linked P, and its leg was S -> X -> P; the only transit node there is B.
        assert did["P"] not in wires["S"].peers()
        assert [leg[2] for d in s.noted("decide") for leg in d["legs"]] == [3]
        # The browser's own count (the page redraws every 250 ms: read until it shows).
        deadline = time.time() + 5
        while True:
            driver.write({"stats": True})
            stats = driver.read()["stats"]
            if stats["carried"] >= 2 or time.time() > deadline:
                break
            time.sleep(0.3)
        assert stats["carried"] >= 2 and stats["status"] == "joined"  # the request out and the answer back

        driver.write({"close": True})
        assert driver.read()["closed"]
        wait(lambda: browser not in s.lsdb.adjacency(time.time()), timeout=20, what="the browser leaving every route")
        assert browser not in s.lsdb.adjacency(time.time()).get(did["P"], set())
        again = parts["U"].ask(["m/a"], "with the browser gone")
        wait(lambda: parts["U"].outcome(again) is not None, timeout=30, what="the second outcome")
        assert parts["U"].outcome(again).status != OUTCOME_ACCEPTED  # no path is left from S to P
    finally:
        stop.set()
        driver.close()
        for w in wires.values():
            w.close()
        signaling.close()
