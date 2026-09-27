"""The headless relay as its own process (2C.1): ``python -m
secdogie_transport.relay_node``, started with stdin closed and nobody to answer
anything, relays between two nodes that know nothing but its signed record.

The relay runs in a real child process on 127.0.0.1 and checks registrations
against its own real clock, so the two clients here use a clock that starts at
real time and can only be pushed forward a little (well inside the relay's
±120 s window) to watch their lease lapse after the relay is gone."""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import (  # noqa: E402
    DirectUDPTransport,
    Endpoint,
    MembershipView,
    PeerIdentity,
    RelayClient,
    Session,
    UDPChannel,
)
from secdogie_transport.membership import ROLE_RELAY, announce, verify_record  # noqa: E402

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RELAY = [sys.executable, "-m", "secdogie_transport.relay_node"]
START_TIMEOUT = 20.0
LEASE = 5.0


class Clock:
    """Real time, plus however far a test has pushed it."""

    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.time() + self.offset

    def advance(self, seconds):
        self.offset += seconds


class Client:
    """A mesh node that uses the relay: direct UDP transport, membership view,
    relay client. It is never told the other node's address."""

    def __init__(self, identity, allow, clock):
        self.identity = identity
        self.did = identity.did
        self.channel = UDPChannel()
        self.transport = DirectUDPTransport(identity, self.channel, allowlist=allow)
        self.view = MembershipView(allowlist=allow)
        self.client = RelayClient(self.transport, self.view, allowlist=allow, clock=clock)
        self.clock = clock
        self.direct_inbox: queue.Queue = queue.Queue()
        self.relay_inbox: queue.Queue = queue.Queue()
        me = Session("s-" + self.did[-6:], PeerIdentity(self.did, "unused"),
                     active=Endpoint("local", *self.channel.address))
        self.transport.register(me, lambda frm, msg: self.direct_inbox.put((frm, msg)))
        self.client.register(me, lambda frm, msg: self.relay_inbox.put((frm, msg)))

    def announce(self) -> dict:
        return announce(self.identity, [Endpoint("local", *self.channel.address)],
                        last_seen=self.clock(), view=self.view, relays=self.client.relays())

    def close(self):
        self.client.close()
        self.channel.close()


def wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not pred():
        time.sleep(0.02)
    return pred()


def get(q, timeout=5.0):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def run_relay(*args, timeout=30.0) -> subprocess.CompletedProcess:
    return subprocess.run(RELAY + list(args), cwd=PACKAGE_ROOT, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout)


@pytest.fixture
def keys(tmp_path):
    """Key files for a relay and two clients, and an allowlist naming all three."""
    relay, a, b = Identity.generate(), Identity.generate(), Identity.generate()
    relay.save(tmp_path / "relay.key")
    allow_file = tmp_path / "mesh.allow"
    allow_file.write_text("".join(f"authorized_did = {i.did}\n" for i in (relay, a, b)), encoding="utf-8")
    return tmp_path, relay, a, b, Allowlist.load(allow_file)


def test_headless_relay_process_forwards_without_anyone_at_the_keyboard(keys):
    tmp, relay, id_a, id_b, allow = keys
    record_file = tmp / "relay.record"
    clock = Clock()
    a = b = None
    proc = subprocess.Popen(
        RELAY + ["--identity", str(tmp / "relay.key"), "--authorized", str(tmp / "mesh.allow"),
                 "--listen", "127.0.0.1:0", "--record-out", str(record_file),
                 "--lease", str(LEASE), "--stats-every", "0.2"],
        cwd=PACKAGE_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert wait(lambda: record_file.exists() or proc.poll() is not None, START_TIMEOUT)
        assert proc.poll() is None, proc.communicate(timeout=5)
        record = json.loads(record_file.read_text(encoding="utf-8"))

        # The bootstrap record is the relay's own, self-signed, offering the role.
        rec = verify_record(record, allowlist=allow)
        assert rec is not None and rec.did == relay.did and rec.roles == (ROLE_RELAY,)
        relay_addr = rec.endpoints.best().key()
        assert relay_addr[0] == "127.0.0.1" and relay_addr[1] > 0

        # A and B know only the relay's record, never each other's address.
        a, b = Client(id_a, allow, clock), Client(id_b, allow, clock)
        for node in (a, b):
            assert node.view.merge_record(record)
            node.client.refresh()
            assert wait(lambda node=node: node.client.relays() == [relay.did])
        assert a.view.merge_record(b.announce())
        assert b.view.merge_record(a.announce())

        assert a.client.route(a.did, b.did, b"via the vps")
        assert get(b.relay_inbox) == (a.did, b"via the vps")
        assert b.client.route(b.did, a.did, b"and back")
        assert get(a.relay_inbox) == (b.did, b"and back")

        # No roaming through the relay: neither side learned any endpoint from
        # relayed traffic -- not the other's, and not the relay's either.
        assert a.transport._endpoints == {} and b.transport._endpoints == {}
        assert get(a.direct_inbox, timeout=0.1) is None and get(b.direct_inbox, timeout=0.1) is None

        assert proc.poll() is None  # still serving, unattended
        proc.terminate()
        out, err = proc.communicate(timeout=10)
        assert proc.returncode == 0, err
        assert out == record_file.read_text(encoding="utf-8")  # the same line went to stdout

        events = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
        assert events[0]["event"] == "started" and events[0]["did"] == relay.did
        assert any(e["event"] == "stats" for e in events)
        assert events[-1]["event"] == "stopped" and events[-1]["forwarded"] == 2

        # The relay is gone; the lease it granted lapses and nothing renews it.
        clock.advance(LEASE + 1)
        assert a.client.relays() == [] and b.client.relays() == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)
        for node in (a, b):
            if node is not None:
                node.close()


def test_relay_process_needs_both_identity_and_allowlist(keys):
    tmp = keys[0]
    missing = run_relay("--identity", str(tmp / "relay.key"), "--listen", "127.0.0.1:0")
    assert missing.returncode == 2 and "--authorized" in missing.stderr
    missing = run_relay("--authorized", str(tmp / "mesh.allow"), "--listen", "127.0.0.1:0")
    assert missing.returncode == 2 and "--identity" in missing.stderr


def test_relay_process_refuses_an_unusable_configuration(keys):
    tmp = keys[0]
    base = ["--identity", str(tmp / "relay.key"), "--authorized", str(tmp / "mesh.allow")]

    wildcard = run_relay(*base, "--listen", "0.0.0.0:0")  # the record would name no reachable host
    assert wildcard.returncode == 2 and "--public-host" in wildcard.stderr

    bad = tmp / "bad.allow"
    bad.write_text("authorized_did = not-a-did\n", encoding="utf-8")
    broken = run_relay("--identity", str(tmp / "relay.key"), "--authorized", str(bad),
                       "--listen", "127.0.0.1:0")
    assert broken.returncode == 2 and "bad.allow" in broken.stderr

    for flag, value in (("--lease", "0"), ("--lease", "601"), ("--stats-every", "-1"), ("--listen", "nope")):
        assert run_relay(*base, "--listen", "127.0.0.1:0", flag, value).returncode == 2, (flag, value)


def test_relay_process_halts_when_its_own_did_is_revoked(keys, tmp_path):
    from secdogie_identity import cosign, create_revocation
    from secdogie_transport.revocation_gossip import REVOCATION_GOSSIP

    tmp, relay, _id_a, _id_b, _allow = keys
    master = Identity.generate()
    masters_file = tmp / "masters.conf"
    masters_file.write_text(f"master_did = {master.did}\n", encoding="utf-8")
    record_file = tmp / "relay.record"

    proc = subprocess.Popen(
        RELAY + ["--identity", str(tmp / "relay.key"), "--authorized", str(tmp / "mesh.allow"),
                 "--listen", "127.0.0.1:0", "--record-out", str(record_file),
                 "--masters", str(masters_file), "--stats-every", "0.2"],
        cwd=PACKAGE_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    sender = UDPChannel()
    try:
        assert wait(lambda: record_file.exists() or proc.poll() is not None, START_TIMEOUT)
        assert proc.poll() is None
        relay_addr = json.loads(record_file.read_text())["endpoints"][0]

        # A master revokes the relay's own DID; the record is gossiped to it.
        # UDP can drop the datagram, so resend on a timer until the process exits
        # or a generous budget elapses -- the halt is what we assert, not its speed.
        rec = cosign(master, create_revocation([relay.did]))
        frame = json.dumps({"t": REVOCATION_GOSSIP, "record": rec}).encode("utf-8")
        code = None
        deadline = time.time() + 30.0
        while time.time() < deadline:
            sender.send(relay_addr["host"], relay_addr["port"], frame)
            try:
                code = proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                continue
        out, err = proc.communicate(timeout=10)
        assert code == 0, err                       # clean, unattended self-halt
        assert any(json.loads(line).get("event") == "halted"
                   for line in err.splitlines() if line.startswith("{")), err
    finally:
        sender.close()
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)


def test_relay_process_carries_no_human_in_the_loop_layer():
    # The relay decides by signatures and the allowlist alone. Structurally: the
    # process imports none of the packages that hold confirmation hooks, and its
    # source never reads from stdin.
    probe = ("import sys, secdogie_transport.relay_node\n"
             "print(sorted(m for m in sys.modules\n"
             "             if m.split('.')[0] in {'secdogie_agent', 'secdogie_citadel', 'secdogie_fleet'}))")
    result = subprocess.run([sys.executable, "-c", probe], cwd=PACKAGE_ROOT, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
    source = (PACKAGE_ROOT / "secdogie_transport" / "relay_node.py").read_text(encoding="utf-8")
    assert "sys.stdin" not in source and "input(" not in source
