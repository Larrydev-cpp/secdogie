"""Stage 3 (mesh), in one process over real UDP on 127.0.0.1: nodes started from
a single bootstrap record find each other by gossip, and their journals
replicate -- so a caution one node earned by failing reaches the others' Gate 1,
while each node still runs only its own goals."""
from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.authz import action_hash  # noqa: E402
from secdogie_citadel.loop_gate import to_planned  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_node import Node, NodeConfig  # noqa: E402
from secdogie_node.node import REPLICATION_CHANNEL  # noqa: E402
from secdogie_transport.membership import verify_record  # noqa: E402

A, B, C, APP, OPERATOR = (Identity.generate() for _ in range(5))
MESH = Allowlist({A.did, B.did, C.did})
CLICK = {"kind": "left_click", "element": None, "x": 10, "y": 20, "text": "", "keys": [], "path": "",
         "high_risk": False, "rollback": "", "irreversible": False}


def _wait(pred, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


class Loop:
    """A fake agent loop: asks the plan gate about one click and records it."""

    def __init__(self, outcome):
        self.outcome, self.gate, self.ran = outcome, [], []

    def __call__(self, task, *, should_stop, on_status, confirm, record_step, plan_gate=None, **_):
        self.ran.append(task)
        allowed, note = plan_gate(CLICK, [])
        self.gate.append((allowed, note))
        record_step(observation={"f": 1}, action=CLICK, result="clicked" if allowed else note,
                    outcome=self.outcome if allowed else "rejected")
        return 0, "done"


@pytest.fixture
def make(tmp_path):
    made = []

    def build(identity, loop, **kw):
        cfg = dict(identity=identity, apps=Allowlist({APP.did}), operators=Allowlist({OPERATOR.did}),
                   authorized=MESH, mesh=MESH, unrestricted=True, run_task=loop, idle_poll=0.05,
                   mesh_every=0.1, journal_path=str(tmp_path / f"{identity.did[-8:]}.db"),
                   candidates_path=str(tmp_path / f"{identity.did[-8:]}.memory"))
        cfg.update(kw)
        node = Node(NodeConfig(**cfg))
        made.append(node)
        return node

    yield build
    for node in made:
        node.stop()


def test_nodes_started_from_one_record_find_each_other(make):
    a = make(A, Loop("ok"))
    b = make(B, Loop("ok"), bootstrap_records=[a.record()])
    c = make(C, Loop("ok"), bootstrap_records=[a.record()])
    for n in (a, b, c):
        n.start()
    assert _wait(lambda: all(sorted(n.peers()) == sorted(d for d in MESH.dids() if d != n.identity.did)
                             for n in (a, b, c)))


def test_a_caution_earned_on_one_node_reaches_the_others_gate(make):
    loop_a, loop_b = Loop("failed"), Loop("ok")
    # One mesh round each, at start (B introduces itself to A); after that the
    # caution travels because A replicates as soon as a goal ends.
    a = make(A, loop_a, mesh_every=60)
    b = make(B, loop_b, bootstrap_records=[a.record()], mesh_every=60)
    a.start()
    b.start()
    assert _wait(lambda: a.peers() == [B.did])
    for i in range(3):
        a.supervisor.add_goal(f"g{i}", "click the toolbar")
    a._wake.set()
    key = action_hash(to_planned(CLICK))
    assert _wait(lambda: key in a.supervisor.memory_view().known_failures)
    assert _wait(lambda: key in b.supervisor.memory_view().known_failures)
    assert loop_b.ran == []  # A's goals reached B's journal, and B ran none of them
    b.supervisor.add_goal("h1", "click the toolbar")
    b._wake.set()
    assert _wait(lambda: loop_b.gate)
    allowed, note = loop_b.gate[0]
    assert not allowed and "failed repeatedly" in note  # refused on B's very first try


def test_a_mesh_node_must_be_a_journal_author(make):
    with pytest.raises(ValueError, match="journal author"):
        make(A, Loop("ok"), authorized=Allowlist({A.did}))


def test_a_bootstrap_record_must_be_a_mesh_nodes(make):
    stranger = Identity.generate()
    other = make(B, Loop("ok"), mesh=Allowlist({B.did, stranger.did}), authorized=Allowlist({B.did, stranger.did}))
    record = other.record()
    with pytest.raises(ValueError, match="bootstrap record"):
        make(A, Loop("ok"), bootstrap_records=[dict(record, did=stranger.did)])  # altered: does not verify
    with pytest.raises(ValueError, match="bootstrap record"):
        make(C, Loop("ok"), mesh=Allowlist({C.did}), authorized=Allowlist({C.did}), bootstrap_records=[record])


def test_an_app_cannot_replicate_into_a_node(make):
    a = make(A, Loop("ok"))
    sent = []
    a.mux.send = lambda to, ch, p: sent.append((to, ch, p)) or True
    have = json.dumps({"kind": "journal_have", "heads": {}, "reply": False}).encode()
    a._on_replication(APP.did, have)
    assert sent == []  # the App shares the transport but gets no journal
    a._on_replication(B.did, have)
    assert [ch for _, ch, _ in sent] == [REPLICATION_CHANNEL, REPLICATION_CHANNEL]


def test_the_own_record_verifies_and_names_the_listen_address(make):
    a = make(A, Loop("ok"))
    rec = verify_record(a.record(), allowlist=MESH)
    assert rec is not None and rec.did == A.did and rec.endpoints.best().key() == a.address


def test_gossip_fills_in_an_unknown_address_but_keeps_a_known_one(make):
    from secdogie_transport import Endpoint
    from secdogie_transport.membership import sign_record

    a = make(A, Loop("ok"))
    rec_b = sign_record(B, [Endpoint("local", "127.0.0.1", 40001)], last_seen=time.time())
    a.gossip._merge([rec_b])
    assert a.transport.peer_endpoint(B.did) == ("127.0.0.1", 40001)  # unknown: taken from the record
    a.transport.set_peer_endpoint(C.did, "127.0.0.1", 40002)  # say, learned from C's own newest frame
    a.gossip._merge([sign_record(C, [Endpoint("local", "127.0.0.1", 40003)], last_seen=time.time())])
    assert a.transport.peer_endpoint(C.did) == ("127.0.0.1", 40002)  # known: kept


def test_the_record_carries_the_address_a_rendezvous_saw(make):
    from secdogie_transport import DirectUDPTransport, Endpoint, RendezvousService, UDPChannel
    from secdogie_transport.membership import ROLE_RENDEZVOUS, sign_record

    rv = Identity.generate()
    channel = UDPChannel("127.0.0.1", 0)
    RendezvousService(DirectUDPTransport(rv, channel, allowlist=MESH), allowlist=MESH)
    rv_record = sign_record(rv, [Endpoint("local", *channel.address)], last_seen=1.0, roles=[ROLE_RENDEZVOUS])
    try:
        a = make(A, Loop("ok"), rendezvous_records=[rv_record])
        a.start()
        assert _wait(lambda: a.rendezvous.reflexive)
        rec = verify_record(a.record(), allowlist=MESH)
        assert ("observed", "127.0.0.1", a.address[1]) in {(e.kind, e.host, e.port) for e in rec.endpoints.all()}
    finally:
        channel.close()
