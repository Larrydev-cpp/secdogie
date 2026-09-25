"""secdogie-node on 127.0.0.1: real UDP between real Node objects.

Rounds are driven by calling `tick()` and waiting until a condition holds (with
a deadline), so the tests are deterministic about *what* must happen without
depending on a timer."""
from __future__ import annotations

import base64
import json
import time

import pytest

pytest.importorskip("nacl")

from nacl.public import PrivateKey  # noqa: E402
from secdogie_citadel.goals import build_goal_tree  # noqa: E402
from secdogie_citadel.state import record_state  # noqa: E402
from secdogie_identity import Identity  # noqa: E402
from secdogie_identity.binding import create_binding  # noqa: E402
from secdogie_node import BINDING_KIND, Node, NodeConfig, NodeConfigError, load_config  # noqa: E402
from secdogie_node.cli import main  # noqa: E402
from secdogie_transport.sealed import public_key_b64  # noqa: E402

# --- helpers -----------------------------------------------------------------


class Mesh:
    """Creates node identities / keys / bindings under tmp_path and builds Nodes."""

    def __init__(self, tmp_path, names, *, encrypted=False, authorized=None):
        self.tmp = tmp_path
        self.encrypted = encrypted
        self.ids: dict[str, Identity] = {}
        self.tkeys: dict[str, PrivateKey] = {}
        self.nodes: dict[str, Node] = {}
        for name in names:
            idn = Identity.generate()
            idn.save(tmp_path / f"{name}.key")
            self.ids[name] = idn
            if encrypted:
                sk = PrivateKey.generate()
                self.tkeys[name] = sk
                (tmp_path / f"{name}.tkey").write_text(
                    f"private_key = {base64.b64encode(bytes(sk)).decode()}\n", encoding="utf-8")
                binding = create_binding(idn, public_key_b64(sk), key_version=1)
                (tmp_path / f"{name}.binding.json").write_text(json.dumps(binding), encoding="utf-8")
        allowed = authorized if authorized is not None else list(names)
        self.write_allowlist("authorized.conf", allowed)

    def write_allowlist(self, filename, names):
        (self.tmp / filename).write_text(
            "".join(f"authorized_did = {self.ids[n].did}\n" for n in names), encoding="utf-8")
        return str(self.tmp / filename)

    def did(self, name):
        return self.ids[name].did

    def start(self, name, *, bindings=(), authorized="authorized.conf", evidence=False):
        cfg = NodeConfig(
            identity=str(self.tmp / f"{name}.key"),
            journal=str(self.tmp / f"{name}.db"),
            listen_host="127.0.0.1",
            listen_port=0,
            authorized=str(self.tmp / authorized),
            transport_key=str(self.tmp / f"{name}.tkey") if self.encrypted else None,
            evidence=str(self.tmp / f"{name}-evidence") if evidence else None,
            bindings=[str(self.tmp / f"{b}.binding.json") for b in bindings],
            sync_interval=0.05,
        )
        node = Node(cfg)
        self.nodes[name] = node
        return node

    def close(self):
        for n in self.nodes.values():
            n.close()


def settle(nodes, cond, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for n in nodes:
            n.tick()
        time.sleep(0.05)
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001 -- a read racing the receive thread; retry
            pass
    return False


def _add_goal(node, gid, title):
    node.journal.append("goal", {"op": "add", "id": gid, "title": title, "deps": []})


def _goals(node):
    return build_goal_tree(node.journal.events()).nodes


# --- config -------------------------------------------------------------------


def test_config_parses_and_resolves_relative_paths(tmp_path):
    did = Identity.generate().did
    (tmp_path / "node.conf").write_text(
        "# a node\n"
        "identity   = keys/node.key\n"
        "journal    = node.db   # sqlite\n"
        "listen     = 0.0.0.0:7400\n"
        "authorized = authorized.conf\n"
        f"peer       = {did} 203.0.113.7:7401\n"
        "binding    = peers/b.json\n"
        "sync_interval = 5\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path / "node.conf")
    assert cfg.identity == str(tmp_path / "keys" / "node.key")
    assert (cfg.listen_host, cfg.listen_port) == ("0.0.0.0", 7400)
    assert cfg.peers == [(did, "203.0.113.7", 7401)]
    assert cfg.bindings == [str(tmp_path / "peers" / "b.json")]
    assert cfg.sync_interval == 5.0 and cfg.transport_key is None


@pytest.mark.parametrize("body, needle", [
    ("identity = a\njournal = b\nlisten = 127.0.0.1:1\n", "missing required key(s): authorized"),
    ("identity = a\nnonsense\n", ":2: expected 'key = value'"),
    ("identity = a\ncolour = blue\n", ":2: unknown key 'colour'"),
    ("peer = not-a-did 127.0.0.1:1\n", ":1:"),
    ("listen = nowhere\n", ":1: expected host:port"),
])
def test_config_errors_name_the_line(tmp_path, body, needle):
    (tmp_path / "bad.conf").write_text(body, encoding="utf-8")
    with pytest.raises(NodeConfigError) as err:
        load_config(tmp_path / "bad.conf")
    assert needle in str(err.value)


# --- the mesh -----------------------------------------------------------------


def test_two_nodes_converge(tmp_path):
    mesh = Mesh(tmp_path, ["a", "b"])
    try:
        a, b = mesh.start("a"), mesh.start("b")
        a.add_peer(b.did, *b.address)  # only A knows B; B learns A from its traffic
        _add_goal(a, "g1", "read the version")
        assert settle([a, b], lambda: "g1" in _goals(b))
        assert _goals(b)["g1"].title == "read the version"
        assert settle([a, b], lambda: a.did in b.status().peers)
    finally:
        mesh.close()


def test_chain_learns_the_far_node_and_talks_to_it_directly(tmp_path):
    mesh = Mesh(tmp_path, ["a", "b", "c"])
    try:
        a, b, c = mesh.start("a"), mesh.start("b"), mesh.start("c")
        a.add_peer(b.did, *b.address)
        c.add_peer(b.did, *b.address)  # A and C only know B
        _add_goal(a, "from-a", "x")
        _add_goal(c, "from-c", "y")
        assert settle([a, b, c], lambda: a.transport.peer_endpoint(c.did) == c.address
                      and "from-c" in _goals(a) and "from-a" in _goals(c))
        # take B away: A and C still reach each other directly
        b.close()
        del mesh.nodes["b"]
        _add_goal(c, "after-b", "z")
        assert settle([a, c], lambda: "after-b" in _goals(a))
    finally:
        mesh.close()


def test_encrypted_mesh_bindings_travel_in_the_journal(tmp_path):
    mesh = Mesh(tmp_path, ["a", "b", "c"], encrypted=True)
    try:
        # Bootstrap bindings only: A and C know B's key, B knows both of theirs.
        a = mesh.start("a", bindings=["b"])
        b = mesh.start("b", bindings=["a", "c"])
        c = mesh.start("c", bindings=["b"])
        assert a.encrypted and b.encrypted and c.encrypted
        sent = []
        orig_send = a.channel.send
        a.channel.send = lambda h, p, d: (sent.append(d), orig_send(h, p, d))[1]

        a.add_peer(b.did, *b.address)
        c.add_peer(b.did, *b.address)
        secret = "the operator's private plan for the week"
        _add_goal(a, "secret", secret)
        # A learns C's key from C's own binding event, replicated C -> B -> A
        assert settle([a, b, c], lambda: c.did in a.transport.peer_keys()
                      and a.transport.peer_endpoint(c.did) == c.address
                      and "secret" in _goals(c), timeout=10.0)
        assert any(e["kind"] == BINDING_KIND and e["author"] == c.did for e in a.journal.events())

        # A and C talk encrypted without B
        b.close()
        del mesh.nodes["b"]
        _add_goal(c, "after-b", "z")
        assert settle([a, c], lambda: "after-b" in _goals(a))

        wire = b"".join(sent)
        assert sent and secret.encode() not in wire
        assert all(json.loads(f)["t"] == "secdogie/direct/v2" for f in sent)
    finally:
        mesh.close()


def test_large_journal_syncs_between_nodes(tmp_path):
    mesh = Mesh(tmp_path, ["a", "b"])
    try:
        a, b = mesh.start("a"), mesh.start("b")
        a.add_peer(b.did, *b.address)
        for i in range(300):
            record_state(a.journal, "knowledge", f"k{i}", "set", {"note": "x" * 500, "i": i})
        assert len(json.dumps(a.journal.events())) > 150_000
        assert settle([a, b], lambda: b.journal.heads().get(a.did) == a.journal.heads()[a.did], timeout=10.0)
    finally:
        mesh.close()


def test_unauthorized_node_is_not_heard(tmp_path):
    mesh = Mesh(tmp_path, ["a", "b", "x"], authorized=["a", "b"])
    mesh.write_allowlist("x-authorized.conf", ["a", "b", "x"])  # X trusts them; they don't trust X
    try:
        a, b = mesh.start("a"), mesh.start("b")
        x = mesh.start("x", authorized="x-authorized.conf")
        a.add_peer(b.did, *b.address)
        x.add_peer(a.did, *a.address)
        _add_goal(x, "intruder", "take over")
        _add_goal(a, "g1", "fine")
        assert settle([a, b, x], lambda: "g1" in _goals(b))
        for _ in range(10):  # plenty of rounds for X to push
            for n in (a, b, x):
                n.tick()
            time.sleep(0.03)
        assert "intruder" not in _goals(a) and "intruder" not in _goals(b)
        assert x.did not in a.peers() and x.did not in b.peers()
        assert all(e["author"] != x.did for e in a.journal.events())
    finally:
        mesh.close()


def test_status_and_cli(tmp_path, capsys):
    mesh = Mesh(tmp_path, ["a"])
    node = mesh.start("a")
    _add_goal(node, "g1", "x")
    st = node.status()
    assert st.did == mesh.did("a") and st.events == 1 and st.heads == {st.did: 1} and not st.encrypted
    mesh.close()

    (tmp_path / "a.conf").write_text(
        "identity = a.key\njournal = a.db\nlisten = 127.0.0.1:0\nauthorized = authorized.conf\n",
        encoding="utf-8")
    assert main(["status", str(tmp_path / "a.conf")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["did"] == mesh.did("a") and out["events"] == 1
    (tmp_path / "bad.conf").write_text("identity = a.key\n", encoding="utf-8")
    assert main(["status", str(tmp_path / "bad.conf")]) == 2


# --- evidence over the mesh --------------------------------------------------


def _learn_evidence(node, data, *, url="https://example.com/big", title="Big"):
    """Store `data` as evidence on `node` and record the knowledge entry."""
    from secdogie_citadel.evidence import record_knowledge
    root = node.evidence.put_bytes(data)
    record_knowledge(node.journal, root=root, url=url, title=title, size=len(data), preview="preview")
    return root


def test_evidence_replicates_over_the_mesh(tmp_path):
    import os
    mesh = Mesh(tmp_path, ["a", "b"])
    try:
        a = mesh.start("a", evidence=True)
        b = mesh.start("b", evidence=True)
        a.add_peer(b.did, *b.address)
        data = os.urandom(3 * 1024 * 1024 + 17)  # a few MB -> many blocks + manifests
        root = _learn_evidence(a, data)
        # the knowledge entry converges, then the blocks follow over rounds
        assert settle([a, b], lambda: b.evidence.has_all(root), timeout=15.0)
        assert b.evidence.read_bytes(root) == data  # byte-for-byte
    finally:
        mesh.close()


def test_evidence_reachable_after_the_origin_leaves(tmp_path):
    import os
    mesh = Mesh(tmp_path, ["a", "b", "c"])
    try:
        a = mesh.start("a", evidence=True)
        b = mesh.start("b", evidence=True)
        c = mesh.start("c", evidence=True)
        a.add_peer(b.did, *b.address)
        b.add_peer(c.did, *c.address)  # a-b-c
        data = os.urandom(1024 * 1024 + 5)
        root = _learn_evidence(a, data)
        assert settle([a, b, c], lambda: b.evidence.has_all(root), timeout=15.0)
        # A leaves; C pulls the evidence from B
        a.close()
        del mesh.nodes["a"]
        c.add_peer(b.did, *b.address)
        assert settle([b, c], lambda: c.evidence.has_all(root), timeout=15.0)
        assert c.evidence.read_bytes(root) == data
    finally:
        mesh.close()


def test_forged_block_is_rejected(tmp_path):
    mesh = Mesh(tmp_path, ["a", "b"])
    try:
        a = mesh.start("a", evidence=True)
        b = mesh.start("b", evidence=True)
        root = a.evidence.put_bytes(b"authentic evidence content")
        # B is fed a block whose bytes don't match the requested hash
        b._on_evidence(a.did, {"kind": "block", "h": root,
                               "d": __import__("base64").b64encode(b"forged!").decode()})
        assert not b.evidence.backend.has(root)  # rejected by the hash check
    finally:
        mesh.close()


def test_evidence_replicates_encrypted(tmp_path):
    import json as _json
    import os
    mesh = Mesh(tmp_path, ["a", "b"], encrypted=True)
    try:
        a = mesh.start("a", bindings=["b"], evidence=True)
        b = mesh.start("b", bindings=["a"], evidence=True)
        sent = []
        orig = a.channel.send
        a.channel.send = lambda h, p, d: (sent.append(d), orig(h, p, d))[1]
        a.add_peer(b.did, *b.address)
        data = os.urandom(200 * 1024)
        root = _learn_evidence(a, data)
        assert settle([a, b], lambda: b.evidence.has_all(root), timeout=15.0)
        assert b.evidence.read_bytes(root) == data
        assert sent and all(_json.loads(f)["t"] == "secdogie/direct/v2" for f in sent)
    finally:
        mesh.close()
