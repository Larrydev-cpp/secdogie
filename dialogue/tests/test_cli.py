"""``secdogie-dialogue``: operator keystores, argument checks, and a headless
run against a node over real UDP on 127.0.0.1."""
from __future__ import annotations

import json
import threading

import pytest

pytest.importorskip("nacl")

from nacl import pwhash  # noqa: E402
from secdogie_citadel.action_gate import PlannedAction  # noqa: E402
from secdogie_citadel.authz import verify_authorization  # noqa: E402
from secdogie_dialogue.cli import main  # noqa: E402
from secdogie_dialogue.keystore import seal_identity  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402

FAST = {"opslimit": pwhash.argon2id.OPSLIMIT_MIN, "memlimit": pwhash.argon2id.MEMLIMIT_MIN}


def test_new_operator_key_and_its_did(tmp_path, capsys):
    pw = tmp_path / "pw"
    pw.write_bytes(b"correct horse\n")
    ks = tmp_path / "op.keystore"
    assert main(["new-operator-key", str(ks), "--passphrase-file", str(pw)]) == 0
    did = capsys.readouterr().out.strip()
    assert did.startswith("did:key:")
    assert main(["operator-did", str(ks)]) == 0
    assert capsys.readouterr().out.strip() == did
    with pytest.raises(SystemExit):  # never overwrites a key
        main(["new-operator-key", str(ks), "--passphrase-file", str(pw)])


def _app_args(tmp_path, node_did="did:key:z6MkNode", *extra):
    key = tmp_path / "app.key"
    if not key.exists():
        Identity.generate().save(key)
    return ["connect", "--identity", str(key), "--node", node_did, "--node-addr", "127.0.0.1:9", *extra]


@pytest.fixture(autouse=True)
def no_screen(monkeypatch):
    """A connect that got past its checks must not open a real screen and wait."""
    try:
        import secdogie_dialogue.tui as tui
    except ImportError:
        return

    def refuse(*a, **k):
        raise AssertionError("the screen was opened")

    monkeypatch.setattr(tui, "run_tui", refuse)


@pytest.mark.parametrize("extra, why", [
    (["--transport-key", "x"], "go together"),  # without the node's binding
    (["--node-binding", "x"], "go together"),  # without our own key
    (["--passphrase-file", "x"], "is for --headless"),
])
def test_connect_refuses_incomplete_or_bad_arguments(tmp_path, capsys, extra, why):
    with pytest.raises(SystemExit) as e:
        main(_app_args(tmp_path, "did:key:z6MkNode", *extra))
    assert e.value.code == 2 and why in capsys.readouterr().err


def test_connect_checks_the_keystore_and_script_before_connecting(tmp_path, capsys):
    script = tmp_path / "s.jsonl"
    script.write_text("")
    with pytest.raises(SystemExit):
        main(_app_args(tmp_path, "did:key:z6MkNode", "--operator-keystore", str(tmp_path / "none"),
                       "--headless", str(script)))
    assert "cannot read keystore" in capsys.readouterr().err
    script.write_text('{"op": "add_goal", "title": "t"}\nnot json\n')
    with pytest.raises(SystemExit):
        main(_app_args(tmp_path, "did:key:z6MkNode", "--headless", str(script)))
    assert "script line 2" in capsys.readouterr().err


def test_connect_needs_to_know_where_the_node_is(tmp_path, capsys):
    Identity.generate().save(tmp_path / "app.key")
    with pytest.raises(SystemExit) as e:
        main(["connect", "--identity", str(tmp_path / "app.key"), "--node", "did:key:z6MkNode"])
    assert e.value.code == 2 and "--rendezvous-record" in capsys.readouterr().err


def test_connect_refuses_a_record_that_is_not_a_rendezvous(tmp_path, capsys):
    pytest.importorskip("secdogie_transport")
    from secdogie_transport import Endpoint
    from secdogie_transport.membership import sign_record

    rec = sign_record(Identity.generate(), [Endpoint("local", "127.0.0.1", 9)], last_seen=1.0)
    (tmp_path / "rv.json").write_text(json.dumps(rec))
    with pytest.raises(SystemExit) as e:
        main(_app_args(tmp_path, "did:key:z6MkNode", "--rendezvous-record", str(tmp_path / "rv.json")))
    assert e.value.code == 2 and "rendezvous role" in capsys.readouterr().err


def test_connect_requires_the_node_did(tmp_path):
    with pytest.raises(SystemExit):
        main(["connect", "--identity", str(tmp_path / "k"), "--node-addr", "127.0.0.1:9"])


def test_headless_run_against_a_node_over_real_udp(tmp_path, capsys):
    pytest.importorskip("secdogie_transport")
    from secdogie_dialogue.agent_bridge import OperatorBridge
    from secdogie_dialogue.session import DialogueSession, SessionRouter
    from secdogie_transport import ChannelMux, DirectUDPTransport, Endpoint, PeerIdentity, Session, UDPChannel

    app_id, node_id, operator = Identity.generate(), Identity.generate(), Identity.generate()
    app_id.save(tmp_path / "app.key")
    seal_identity(operator, b"s3cret", tmp_path / "op.keystore", **FAST)
    (tmp_path / "pw").write_bytes(b"s3cret\n")

    # the node: one UDP transport, a dialogue router that accepts only this App, and the bridge
    ch = UDPChannel("127.0.0.1", 0)
    tr = DirectUDPTransport(node_id, ch, allowlist=Allowlist({app_id.did}))
    mux = ChannelMux(tr, Session("node", PeerIdentity(node_id.did, ""), active=Endpoint("local", *ch.address)))
    bridges, ready = [], threading.Event()
    router = None

    def accept(did):
        if did != app_id.did:
            return None
        s = DialogueSession(node_id, did, router.sender_for(did), trust=Allowlist({app_id.did}))
        bridges.append(OperatorBridge(node_id, s, operators=Allowlist({operator.did}), challenge_ttl=10,
                                      on_control=lambda pkt, signer: "accepted"))
        s.start(0.05)
        ready.set()
        return s

    router = SessionRouter(mux, accept=accept)
    planned = PlannedAction("delete", "element:4", "file", "report.txt", "", True)
    got = {}

    def node_side():
        if ready.wait(10):
            got["token"] = bridges[0].authorize(planned)

    t = threading.Thread(target=node_side, daemon=True)
    t.start()
    script = tmp_path / "script.jsonl"
    script.write_text("\n".join([
        "# the operator's steps",
        json.dumps({"op": "add_goal", "title": "file the report", "goal_id": "g1"}),
        json.dumps({"op": "approve", "action": {"kind": "delete", "target_name": "report.txt"}}),
    ]) + "\n")
    try:
        rc = main(["connect", "--identity", str(tmp_path / "app.key"), "--node", node_id.did,
                   "--node-addr", f"127.0.0.1:{ch.address[1]}", "--listen", "127.0.0.1:0",
                   "--operator-keystore", str(tmp_path / "op.keystore"),
                   "--passphrase-file", str(tmp_path / "pw"), "--headless", str(script), "--step-timeout", "10"])
        t.join(10)
    finally:
        for s in router.sessions():
            s.close()
        ch.close()
    results = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rc == 0, results
    assert [r["op"] for r in results] == ["add_goal", "approve"] and all(r["ok"] for r in results)
    assert results[0]["reply"] == "accepted"
    res = verify_authorization(got["token"], planned, operators=Allowlist({operator.did}), subject=node_id.did)
    assert res.ok, res.reason
