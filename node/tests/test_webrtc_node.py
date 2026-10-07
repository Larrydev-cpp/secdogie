"""The browser link end to end, in one process, with a Python stand-in for the
page: ``pair`` enrolls it (owner "y" + tap), the resident node picks the new
App up from its allowlist file without a restart, the page attaches in the
resident room over W1, says HELLO and is told what the node is doing; a
Gate 2 challenge raised while no page is attached waits for it and is shown
again when it arrives."""
from __future__ import annotations

import queue
import socket
import threading
import time

import pytest

pytest.importorskip("nacl")
pytest.importorskip("aiortc")
pytest.importorskip("websockets")

from secdogie_citadel.authz import action_hash  # noqa: E402
from secdogie_citadel.loop_gate import to_planned  # noqa: E402
from secdogie_dialogue.guard import respond  # noqa: E402
from secdogie_dialogue.protocol import (  # noqa: E402
    DialoguePacket,
    DialogueType,
    Gate2ChallengePacket,
    SessionEvent,
    SessionPacket,
    Verdict,
)
from secdogie_dialogue.session import DialogueSession, SessionRouter  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_identity import linkauth as la  # noqa: E402
from secdogie_node import Node, NodeConfig  # noqa: E402
from secdogie_node.pairing import PairingOffer, PairingPolicy, file_enroller  # noqa: E402
from secdogie_transport import (  # noqa: E402
    WEBRTC_HOST,
    ChannelMux,
    CompositeChannel,
    DirectUDPTransport,
    Endpoint,
    PeerIdentity,
    Session,
    UDPChannel,
)
from secdogie_transport.webrtc import BindingPolicy, WebRTCChannel, WebRTCConfig  # noqa: E402
from secdogie_transport.webrtc_testing import FakeSignalingServer  # noqa: E402


def _lan_ipv4() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return not s.getsockname()[0].startswith("127.")
    except OSError:
        return False
    finally:
        s.close()


pytestmark = pytest.mark.skipif(not _lan_ipv4(), reason="aiortc's ICE needs a non-loopback IPv4 address")

DELETE = {"kind": "key", "element": None, "x": None, "y": None, "text": "", "keys": ["delete"], "path": "",
          "high_risk": True, "rollback": "", "irreversible": True}


def _until(pred, timeout=20.0, what="condition"):
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.05)


class PagePairing:
    """What the page does with a pairing link: check the node, say hello, tap."""

    def __init__(self, app, operator, invite):
        self.app, self.operator, self.invite = app, operator, invite
        self.code = None
        self.paired = None

    def on_open(self, link):
        pass  # the page says nothing before the node's statement verifies

    def on_text(self, link, msg):
        t = msg.get("type")
        if t == la.LINK_BINDING_TYPE:
            r = la.verify_link_binding(msg, trust=Allowlist({self.invite.node_did}), room=link.room,
                                       observed_local=link.local_fingerprints,
                                       observed_remote=link.remote_fingerprints)
            assert r.ok, r.reason
            hello = la.create_pair_hello(self.app, secret=self.invite.secret, node_did=self.invite.node_did,
                                         local=link.local_fingerprints, remote=link.remote_fingerprints,
                                         operator_did=self.operator.did)
            self.code = la.check_code(hello)
            proof = la.create_operator_proof(self.operator, app_did=self.app.did, node_did=self.invite.node_did,
                                             pairing=self.invite.pairing_id)
            link.send_text(hello)
            link.send_text(la.create_pair_confirm(self.app, secret=self.invite.secret,
                                                  node_did=self.invite.node_did, hello=hello, operator_proof=proof))
        elif t == la.PAIRED_TYPE:
            self.paired = la.verify_paired(msg, node_did=self.invite.node_did, app_did=self.app.did,
                                           pairing=self.invite.pairing_id, observed_local=link.local_fingerprints,
                                           observed_remote=link.remote_fingerprints)


class Page:
    """The attached page: W1, then a dialogue session over the link."""

    def __init__(self, app, node_did, url, room):
        trust = Allowlist({node_did})
        self.policy = BindingPolicy(app, trust, speak_first=False, node_did=node_did)
        self.link = WebRTCChannel(WebRTCConfig(url, room, ice_servers=()), self.policy)
        self.channel = CompositeChannel(UDPChannel("127.0.0.1", 0), {WEBRTC_HOST: self.link})
        self.transport = DirectUDPTransport(app, self.channel, allowlist=trust,
                                            bound_link_hosts=frozenset({WEBRTC_HOST}))
        self.mux = ChannelMux(self.transport, Session("p", PeerIdentity(app.did, ""),
                                                      active=Endpoint("local", *self.channel.address)))
        self.router = SessionRouter(self.mux)
        self.session = self.router.add(DialogueSession(app, node_did, self.router.sender_for(node_did), trust=trust))
        self.seen: queue.Queue = queue.Queue()
        self.session.on_envelope = lambda env: self.seen.put(env.packet)
        self.node_did = node_did

    def attach(self):
        self.link.open()
        _until(lambda: any(k == "bound" for k, _ in self.link.events), what="the page bound")
        link_id = [d for k, d in self.link.events if k == "bound"][-1][0]
        self.transport.set_peer_endpoint(self.node_did, WEBRTC_HOST, link_id)
        self.session.start(0.05)
        self.session.send(SessionPacket(SessionEvent.HELLO))

    def next(self, kind, timeout=15.0):
        deadline = time.monotonic() + timeout
        while True:
            pkt = self.seen.get(timeout=max(0.01, deadline - time.monotonic()))
            if isinstance(pkt, kind):
                return pkt

    def close(self):
        self.channel.close()


def test_pair_then_attach_then_status_and_a_challenge_that_waited(tmp_path):
    server = FakeSignalingServer()
    server.start()
    node_id, app, operator = Identity.generate(), Identity.generate(), Identity.generate()
    apps_file, ops_file = tmp_path / "apps.allow", tmp_path / "ops.allow"
    apps_file.write_text("", encoding="utf-8")
    ops_file.write_text("", encoding="utf-8")
    apps, ops = Allowlist.load(apps_file), Allowlist.load(ops_file)
    room = la.derive_room(node_id)
    node = Node(NodeConfig(identity=node_id, apps=apps, operators=ops, authorized=Allowlist({node_id.did}),
                           run_task=lambda *a, **k: (0, "ok"), idle_poll=0.05, challenge_ttl=20.0,
                           webrtc=WebRTCConfig(server.url, room, ice_servers=()), apps_file=str(apps_file),
                           operators_file=str(ops_file)))
    pairing = page = None
    try:
        node.start()
        _until(lambda: server.peers(room) == 1, what="the node in its room")

        # -- pair (the pair command's policy, in this process)
        offer = PairingOffer(ttl=120)
        asked = []
        policy = PairingPolicy(node_id, offer, standing_room=room, ask=lambda q: asked.append(q) or True,
                               enroll=file_enroller(str(apps_file), str(ops_file), pairing_id=offer.pairing_id))
        pairing = WebRTCChannel(WebRTCConfig(server.url, offer.room, ice_servers=(), bind_timeout=120), policy)
        pairing.start(lambda d, i: None)
        pairing.open()
        invite = la.parse_pairing_fragment(offer.link("https://ui.example/", node_id.did).split("#", 1)[1])
        page_pairing = PagePairing(app, operator, invite)
        page_side = WebRTCChannel(WebRTCConfig(server.url, invite.room, ice_servers=()), page_pairing)
        page_side.start(lambda d, i: None)
        page_side.open()
        _until(lambda: page_pairing.paired is not None, what="the receipt")
        assert page_pairing.paired.ok and page_pairing.paired.room == room
        assert page_pairing.code in asked[0]
        page_side.close()
        pairing.close()
        pairing = None

        # -- the resident node hears the new App without a restart: the page attaches at once and
        #    the node re-reads its allowlist before it would call the page a stranger
        assert not apps.contains(app.did)

        # -- a destructive step while no page is attached: it waits for one
        result = {}
        threading.Thread(target=lambda: result.setdefault("token", node.bridge.authorize(to_planned(DELETE))),
                         daemon=True).start()
        _until(lambda: node.bridge.waiting(), what="the challenge")
        node._running = "g-1"

        # -- the page attaches in the resident room and says hello
        page = Page(app, node_id.did, server.url, room)
        page.attach()
        status = page.next(DialoguePacket)
        assert (status.dialogue_type, status.content, status.in_reply_to) == \
            (DialogueType.SYSTEM_STATUS, "status: waiting for operator", "g-1")
        challenge = page.next(Gate2ChallengePacket)
        assert challenge.action_hash == action_hash(to_planned(DELETE))
        page.session.send(respond(challenge, Verdict.APPROVE, peer_did=node_id.did, operator=operator))
        _until(lambda: "token" in result, what="the authorization")
        assert result["token"] is not None and result["token"]["signer"] == operator.did
        assert node.transport.endpoint_host(app.did) == WEBRTC_HOST
        assert apps.contains(app.did) and ops.contains(operator.did)
        reloads = [e for e in node.journal.events() if e["kind"] == "enrollment"]
        assert {e["body"]["list"] for e in reloads} == {"apps", "operators"}
    finally:
        node._running = None
        if page is not None:
            page.close()
        if pairing is not None:
            pairing.close()
        node.stop()
        server.stop()
