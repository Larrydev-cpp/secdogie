"""The key broker across restarts.

An exposed key that cannot be rotated stays dark however the key source numbers
it after a restart; lease versions keep rising so nothing issued before a
restart outranks what is issued after; and a node convicted after a restart
still loses the leases it got before it, and its keys are still rotated.
"""
from __future__ import annotations

import base64
import json
import os
import stat

import pytest

pytest.importorskip("nacl")

from nacl.public import PrivateKey  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_inference import (  # noqa: E402
    Assignment,
    BrokerStore,
    KeyBroker,
    MemoryKeySource,
    key_fingerprint,
)
from secdogie_inference.lease import LEASE_REVOCATION, make_lease, new_canary, open_grant  # noqa: E402
from secdogie_inference.mutiny import PROVIDER_EQUIVOCATION, make_incident  # noqa: E402
from secdogie_inference.protocol import (  # noqa: E402
    TrustRoots,
    make_decision,
    make_heartbeat,
    make_request,
    make_response,
)
from secdogie_inference.router import Leg  # noqa: E402

M = "m/a"


class Wire:
    def __init__(self):
        self.frames: list[tuple[str, dict]] = []

    def send(self, to_did, payload):
        self.frames.append((to_did, json.loads(payload)))
        return True

    def connect(self, did):
        pass


class World:
    """Broker ``k`` lends ``m/a`` to model nodes ``p`` and ``q``; ``p`` turns out
    to equivocate. ``s`` decides, ``op`` is the operator."""

    def __init__(self, tmp_path):
        self.t = 1_000_000.0
        self.ids = {n: Identity.generate() for n in ("user", "s", "p", "q", "w", "k", "op")}
        d = self.did = {n: i.did for n, i in self.ids.items()}
        self.trust = TrustRoots(mesh=Allowlist({d[n] for n in ("s", "p", "q", "w", "k")}),
                                users=Allowlist({d["user"]}), deciders=Allowlist({d["s"]}),
                                coordinators=Allowlist({d["s"]}), brokers=Allowlist({d["k"]}),
                                operators=Allowlist({d["op"]}))
        self.tkeys = {n: PrivateKey.generate() for n in ("p", "q")}
        self.assignments = {d[n]: Assignment(base64.b64encode(bytes(self.tkeys[n].public_key)).decode(), (M,))
                            for n in ("p", "q")}
        self.store = BrokerStore(tmp_path / "broker.jsonl")
        self.seq = 0

    def clock(self):
        return self.t

    def broker(self, keys, *, store=True):
        """A (re)started broker: fresh process memory, the same store on disk."""
        wire = Wire()
        b = KeyBroker(self.ids["k"], wire, self.trust, keys, assignments=self.assignments, clock=self.clock,
                      broker_store=self.store if store else None)
        b.wire_log = wire
        return b

    def grants(self, broker):
        """(node name, lease, opened secret) for every grant the broker sent."""
        name = {v: k for k, v in self.did.items()}
        out = []
        for to, f in broker.wire_log.frames:
            if f["k"] == "grant":
                opened = open_grant(f["lease"], f["sealed"], self.tkeys[name[to]])
                out.append((name[to], f["lease"], opened.secret))
        return out

    def revoked(self, broker) -> set[str]:
        return {lid for _to, f in broker.wire_log.frames
                if f["k"] == "flood" and f["obj"].get("type") == LEASE_REVOCATION
                for lid in f["obj"]["lease_ids"]}

    def heartbeat_from_p(self, broker, leases):
        """``p`` advertises the leases it holds, as it does every heartbeat."""
        self.seq += 1
        hb = make_heartbeat(self.ids["p"], roles={"model"}, seq=self.seq, neighbors=[], leases=leases,
                            clock=self.clock)
        assert broker.absorb(hb)

    def p_equivocates(self, broker):
        lease = make_lease(self.ids["k"], self.did["p"], M, key_version=1, canary=new_canary(), clock=self.clock)
        req = make_request(self.ids["user"], [M], "hi", clock=self.clock)
        dec = make_decision(self.ids["s"], req, [Leg(self.did["p"], M, (self.did["s"], self.did["p"]))],
                            mode="normal", epoch=1, clock=self.clock)
        a, b = (make_response(self.ids["p"], req, dec, M, lease, output=o, clock=self.clock) for o in ("1", "2"))
        assert broker.absorb(make_incident(self.ids["s"], PROVIDER_EQUIVOCATION, {"a": a, "b": b},
                                           clock=self.clock))


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def test_an_exposed_key_that_cannot_rotate_stays_dark_across_restarts(w):
    first = w.broker(MemoryKeySource({M: "sk-1"}))   # no mint: this source cannot rotate
    first.on_tick(w.t)
    assert sorted((n, s) for n, _, s in w.grants(first)) == [("p", "sk-1"), ("q", "sk-1")]

    w.p_equivocates(first)
    assert first.exposed() == {key_fingerprint("sk-1"): M}
    assert len(w.revoked(first)) == 2      # p's lease and q's: neither stands on an exposed key
    w.t += 1
    first.on_tick(w.t)
    assert len(w.grants(first)) == 2       # dark: nothing new went out

    # The operator restarts with the same config. The source calls the key version 1 again.
    again = w.broker(MemoryKeySource({M: "sk-1"}))
    again.on_tick(w.t)
    assert w.grants(again) == []           # still dark: q does not get the exposed key back

    # Only a new key brings the model back.
    fixed = w.broker(MemoryKeySource({M: "sk-2"}))
    fixed.on_tick(w.t)
    assert sorted((n, s) for n, _, s in w.grants(fixed)) == [("p", "sk-2"), ("q", "sk-2")]


def test_without_a_store_a_restart_would_lend_the_exposed_key_again(w):
    """The failure the store exists for, pinned down."""
    first = w.broker(MemoryKeySource({M: "sk-1"}), store=False)
    first.on_tick(w.t)
    w.p_equivocates(first)
    again = w.broker(MemoryKeySource({M: "sk-1"}), store=False)
    again.on_tick(w.t)
    assert ("q", "sk-1") in [(n, s) for n, _, s in w.grants(again)]


def test_lease_versions_keep_rising_across_restarts(w):
    keys = MemoryKeySource({M: "sk-1"})
    first = w.broker(keys)
    first.on_tick(w.t)
    keys.set(M, "sk-2")                    # rotated by hand
    first.on_tick(w.t)
    assert sorted({lease["key_version"] for _, lease, _ in w.grants(first)}) == [1, 2]

    # The restarted source numbers "sk-2" as version 1. Its leases must still outrank
    # the version-2 leases issued before the restart, which nodes may still hold.
    again = w.broker(MemoryKeySource({M: "sk-2"}))
    again.on_tick(w.t)
    assert {lease["key_version"] for _, lease, _ in w.grants(again)} == {3}

    unchanged = w.broker(MemoryKeySource({M: "sk-2"}), store=False)
    unchanged.on_tick(w.t)
    assert {lease["key_version"] for _, lease, _ in w.grants(unchanged)} == {1}  # what it was before


def test_a_node_convicted_after_a_restart_still_loses_its_leases_and_keys(w):
    keys = MemoryKeySource({M: "sk-1"}, mint=lambda m: "sk-rotated")
    first = w.broker(keys)
    first.on_tick(w.t)
    p_lease = next(lease for n, lease, _ in w.grants(first) if n == "p")

    again = w.broker(keys)                 # restarted: it issued nothing yet in this process
    w.heartbeat_from_p(again, [p_lease])   # but p advertises the lease it still holds
    w.p_equivocates(again)

    assert p_lease["lease_id"] in w.revoked(again)
    assert keys.current(M) == (2, "sk-rotated")      # p had the key: rotated
    assert key_fingerprint("sk-1") in again.exposed()
    assert [n for n, _, s in w.grants(again)] == ["q"] and w.grants(again)[0][2] == "sk-rotated"


def test_a_node_convicted_before_any_heartbeat_still_has_its_keys_rotated(w):
    """Right after a restart the broker may know none of p's leases. p was
    assigned m/a, so it may hold m/a's key: rotate anyway."""
    keys = MemoryKeySource({M: "sk-1"}, mint=lambda m: "sk-rotated")
    w.broker(keys).on_tick(w.t)
    again = w.broker(keys)
    w.p_equivocates(again)
    assert keys.current(M) == (2, "sk-rotated")
    assert key_fingerprint("sk-1") in again.exposed()


def test_store_lines_can_only_darken_keys_or_raise_versions(w):
    for rec in ({"t": "exposed", "model": M, "fp": "zz"},                 # not a fingerprint
                {"t": "exposed", "model": "bad model!", "fp": "a" * 64},
                {"t": "unexposed", "model": M, "fp": key_fingerprint("sk-1")},  # no such record
                {"t": "version", "model": M, "v": -5},
                {"t": "version", "model": M, "v": True},
                {"t": "version", "model": M, "v": 2 ** 60},
                {"t": "version", "model": M, "v": 9},
                ["not", "a", "record"]):
        w.store.append(rec)
    with w.store.path.open("a", encoding="utf-8") as f:
        f.write('{"t": "exposed", "model": "m/a", "fp": "ab\n')       # torn

    b = w.broker(MemoryKeySource({M: "sk-1"}))
    b.on_tick(w.t)
    assert b.exposed() == {}
    assert {lease["key_version"] for _, lease, _ in w.grants(b)} == {10}

    w.store.append({"t": "exposed", "model": M, "fp": key_fingerprint("sk-1")})
    dark = w.broker(MemoryKeySource({M: "sk-1"}))
    dark.on_tick(w.t)
    assert w.grants(dark) == []            # a line can darken a key


def test_the_store_holds_fingerprints_not_secrets(w):
    b = w.broker(MemoryKeySource({M: "sk-very-secret"}))
    b.on_tick(w.t)
    w.p_equivocates(b)
    text = w.store.path.read_text(encoding="utf-8")
    assert "sk-very-secret" not in text and key_fingerprint("sk-very-secret") in text
    assert stat.S_IMODE(os.stat(w.store.path).st_mode) == 0o600


def test_a_failing_store_still_darkens_in_memory(w, tmp_path):
    class Broken(BrokerStore):
        def append(self, obj):
            raise OSError("disk full")

    w.store = Broken(tmp_path / "x.jsonl")
    b = w.broker(MemoryKeySource({M: "sk-1"}))
    b.on_tick(w.t)
    w.p_equivocates(b)
    assert key_fingerprint("sk-1") in b.exposed() and b.store_errors > 0
    b.on_tick(w.t + 1)
    assert len(w.grants(b)) == 2           # only the two from before the incident
