"""The core <-> node wire messages: round-trip and strict decode."""

from __future__ import annotations

import pytest
from secdogie_swarm import wire


def test_each_message_round_trips() -> None:
    msgs = [
        wire.dispatch({"task_id": "t", "signer": "did:key:z", "sig": "x"}),
        wire.dispatch({"task_id": "t"}, resume_from={"pages_done": 2}),
        wire.result({"task_id": "t", "node_did": "did:key:z", "output": {}}),
        wire.heartbeat("did:key:z"),
        wire.probe("n1", 10),
        wire.pong("did:key:z", "n1"),
        wire.checkpoint("t", "did:key:z", {"pages_done": 3}),
        wire.cancel("t", "migrated"),
    ]
    for m in msgs:
        assert wire.decode(wire.encode(m)) == m


def test_decode_rejects_bad_messages() -> None:
    good = wire.encode(wire.heartbeat("did:key:z")).decode()
    with pytest.raises(wire.WireError):
        wire.decode(good.replace(wire.WIRE_PROTOCOL, "secdogie/swarm-wire/v9"))  # wrong protocol
    with pytest.raises(wire.WireError):
        wire.decode('{"v":"secdogie/swarm-wire/v1","type":"NOPE"}')  # unknown type
    with pytest.raises(wire.WireError):
        wire.decode('{"v":"secdogie/swarm-wire/v1","type":"HEARTBEAT"}')  # missing node_did
    with pytest.raises(wire.WireError):
        wire.decode('{"v":"secdogie/swarm-wire/v1","type":"PROBE","nonce":"n","sent_at_ms":NaN}')  # NaN
    with pytest.raises(wire.WireError):
        wire.decode('{"v":"secdogie/swarm-wire/v1","v":"x","type":"HEARTBEAT","node_did":"d"}')  # dup key
