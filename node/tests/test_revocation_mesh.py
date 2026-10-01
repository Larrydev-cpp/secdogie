"""Stage 3, Wave H, in one process over real UDP on 127.0.0.1.

T6 -- revocations last and reach every node: a Master-signed revocation a node
learns (by the fast gossip frame, from the operator's store, or from the
journal) is applied to every trust set it holds and written into its journal,
so a node that was offline catches up by replication when it joins; a revoked
node halts; a forged record changes nothing on any path.

T7 -- a headless node takes no goals and never loads the agent."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.revocations import publish_revocation, revocation_events  # noqa: E402
from secdogie_dialogue.protocol import ControlOp, ControlPacket  # noqa: E402
from secdogie_identity import (  # noqa: E402
    Allowlist,
    Identity,
    MasterSet,
    RevocationStore,
    TrustPolicy,
    cosign,
    create_revocation,
)
from secdogie_node import Node, NodeConfig  # noqa: E402
from secdogie_transport import UDPChannel  # noqa: E402
from secdogie_transport.membership import verify_record  # noqa: E402

A, B, C, APP, OPERATOR = (Identity.generate() for _ in range(5))
MASTER, OTHER_MASTER = Identity.generate(), Identity.generate()
MASTERS = MasterSet([MASTER.did, OTHER_MASTER.did], threshold=2)
NODES = {A.did, B.did, C.did}


def _wait(pred, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def under_threshold(*dids):
    return cosign(MASTER, create_revocation([d.did for d in dids]))  # one of the two signatures


def revocation(*dids):
    return cosign(OTHER_MASTER, under_threshold(*dids))


def _policy(dids):
    return TrustPolicy(Allowlist(set(dids)), masters=MASTERS)


@pytest.fixture
def make(tmp_path):
    made = []

    def build(identity, **kw):
        halted = []
        cfg = dict(identity=identity, apps=_policy({APP.did}), operators=_policy({OPERATOR.did}),
                   authorized=_policy(NODES), mesh=_policy(NODES), unrestricted=True,
                   run_task=lambda *a, **k: (0, "ok"), idle_poll=0.05, mesh_every=0.1, masters=MASTERS,
                   journal_path=str(tmp_path / f"{identity.did[-8:]}.db"),
                   candidates_path=str(tmp_path / f"{identity.did[-8:]}.memory"),
                   on_self_revoked=lambda: halted.append(True))
        cfg.update(kw)
        node = Node(NodeConfig(**cfg))
        node.halted_by_revocation = halted
        made.append(node)
        return node

    yield build
    for node in made:
        node.stop()


def test_a_node_that_was_offline_catches_up_on_a_revocation(make):
    a = make(A)
    a.start()
    record = revocation(C)
    assert a.apply_revocation(record) == {C.did}
    assert not a.mesh.contains(C.did) and not a.transport._allowlist.contains(C.did)
    assert len(revocation_events(a.journal.events())) == 1  # written down, so it lasts
    assert a.apply_revocation(record) == frozenset()  # once
    assert len(revocation_events(a.journal.events())) == 1
    # B was not running when it happened; it joins later and learns it from A's journal
    b = make(B, bootstrap_records=[a.record()])
    assert b.mesh.contains(C.did)
    b.start()
    assert _wait(lambda: not b.mesh.contains(C.did))
    assert not b.transport._allowlist.contains(C.did)  # C's frames are no longer heard
    assert len(revocation_events(b.journal.events())) == 1  # carried, not written again


def test_a_node_revoked_while_offline_halts_when_it_rejoins(make):
    a = make(A)
    a.start()
    a.apply_revocation(revocation(B))
    b = make(B, bootstrap_records=[a.record()])
    b.start()
    assert _wait(lambda: b.halted_by_revocation == [True])
    assert b.supervisor.halted


def test_a_forged_revocation_changes_nothing_on_any_path(make, tmp_path):
    a = make(A)
    b = make(B, bootstrap_records=[a.record()])
    forged = under_threshold(C)
    assert a.apply_revocation(forged) == frozenset()
    assert revocation_events(a.journal.events()) == []  # not written down either
    publish_revocation(a.journal, forged)  # a mesh node writes it down anyway
    a.start()
    b.start()
    assert _wait(lambda: len(revocation_events(b.journal.events())) == 1)  # it replicates...
    time.sleep(0.3)
    assert b.mesh.contains(C.did)  # ...and is refused where it is applied
    # the genuine record, with the forged one's id already in the journal, still gets through
    genuine = cosign(OTHER_MASTER, forged)
    assert genuine["record_id"] == forged["record_id"]
    assert a.apply_revocation(genuine) == {C.did}
    assert _wait(lambda: not b.mesh.contains(C.did))


def test_the_fast_gossip_frame_and_the_operators_store_both_reach_the_journal(make, tmp_path):
    store = RevocationStore(tmp_path / "revocations.jsonl")
    a = make(A, revocation_store=store)
    a.start()
    sender = UDPChannel("127.0.0.1", 0)
    try:
        frame = json.dumps({"t": "secdogie/revocation/gossip/v1", "record": revocation(C)}).encode()
        sender.send(*a.address, frame)
        assert _wait(lambda: not a.mesh.contains(C.did))
    finally:
        sender.close()
    store.append(revocation(B))  # the operator's revoke-apply
    assert _wait(lambda: not a.mesh.contains(B.did))
    revoked = sorted(d for e in revocation_events(a.journal.events()) for d in e["body"]["revoked"])
    assert revoked == sorted([B.did, C.did])


def test_a_revoked_app_loses_its_session(make):
    a = make(A)
    session = a._accept(APP.did)
    assert session is not None and a._link is not None
    a.apply_revocation(revocation(APP))
    assert a._link is None
    assert a._accept(APP.did) is None  # and cannot come back


def test_without_masters_no_revocation_is_applied_or_carried(make):
    a = make(A, masters=None)
    assert a.apply_revocation(revocation(C)) == frozenset()
    assert revocation_events(a.journal.events()) == []


# ---- T7: device class ---------------------------------------------------------------


def test_a_headless_node_takes_no_goals_and_says_so_in_its_record(make):
    ran = []
    h = make(A, device_class="headless", run_task=lambda *a, **k: ran.append(a) or (0, "ok"))
    h.start()
    reply = h.on_control(ControlPacket(request_id="r1", op=ControlOp.ADD_GOAL, goal_id="g1", title="click things"), APP.did)
    assert reply.startswith("refused: headless node")
    h.supervisor.add_goal("g2", "queued some other way")
    assert h.run_ready_once() is False  # the worker is not even running
    code, summary = h.supervisor.run_goal("g2")
    assert code == 1 and "headless" in summary and ran == []  # the runner is a refusal
    assert verify_record(h.record(), allowlist=Allowlist(NODES)).device_class == "headless"


def test_a_display_node_says_so_in_its_record(make):
    d = make(A)
    assert verify_record(d.record(), allowlist=Allowlist(NODES)).device_class == "display"


def test_an_unknown_device_class_is_refused(make):
    with pytest.raises(ValueError, match="device_class"):
        make(A, device_class="robot")


def test_a_headless_node_never_loads_the_agent(tmp_path):
    """In a fresh interpreter: a headless node that was asked for a goal, and
    had one queued, has not imported the agent package at all."""
    script = tmp_path / "headless.py"
    script.write_text(f"""
import sys
from secdogie_identity import Allowlist, Identity
from secdogie_dialogue.protocol import ControlOp, ControlPacket
from secdogie_node import Node, NodeConfig
me = Identity.generate()
node = Node(NodeConfig(identity=me, apps=Allowlist(), operators=Allowlist(), authorized=Allowlist({{me.did}}),
                       mesh=Allowlist({{me.did}}), device_class="headless", unrestricted=True,
                       journal_path={str(tmp_path / 'h.db')!r}, candidates_path={str(tmp_path / 'h.mem')!r}))
node.start()
print(node.on_control(ControlPacket(request_id="r1", op=ControlOp.ADD_GOAL, goal_id="g1", title="t"), "did:key:x"))
node.supervisor.add_goal("g2", "t")
print(node.supervisor.run_goal("g2"))
node.stop()
print(sorted(m for m in sys.modules if m.startswith("secdogie_agent")))
""", encoding="utf-8")
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60,
                         cwd=Path(__file__).parent)
    assert out.returncode == 0, out.stderr[-2000:]
    lines = out.stdout.strip().splitlines()
    assert lines[0].startswith("refused: headless node")
    assert lines[-1] == "[]"


def test_a_knocking_revoked_did_is_told_only_its_own_records_and_not_too_often(make):
    a = make(A)
    rec_b, rec_c = revocation(B), revocation(C)
    a.apply_revocation(rec_b)
    a.apply_revocation(rec_c)
    sent = []
    a.channel.send = lambda host, port, data: sent.append(json.loads(data))
    for _ in range(5):
        a._tell_the_revoked(B.did, ("127.0.0.1", 9))
    assert [m["record"]["record_id"] for m in sent] == [rec_b["record_id"]]
    a._tell_the_revoked(OPERATOR.did, ("127.0.0.1", 9))  # never revoked: nothing to say
    assert len(sent) == 1


def test_a_node_floods_a_revocation_it_learns_to_the_nodes_it_knows(make):
    b = make(B)
    a = make(A, bootstrap_records=[b.record()])  # a knows where b is
    sent = []
    real = a.channel.send
    a.channel.send = lambda host, port, data: sent.append((port, data)) or real(host, port, data)
    record = revocation(C)
    a.apply_revocation(record)  # from the store or the journal: no gossip frame came in
    frames = [json.loads(d) for p, d in sent if p == b.address[1]]
    assert [f["record"]["record_id"] for f in frames if f.get("t") == "secdogie/revocation/gossip/v1"] \
        == [record["record_id"]]
    sent.clear()
    a.apply_revocation(record)
    assert sent == []  # once
