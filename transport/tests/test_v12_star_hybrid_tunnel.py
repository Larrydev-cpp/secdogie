"""Phase 3 unit tests: Hub-and-Spoke star topology, 3-tier auto-fallback encrypted
tunnel (LAN -> WebRTC P2P -> E2EE blind relay), and seq_id resumable idempotent queue.
"""
from __future__ import annotations

import pytest
from secdogie_identity import Identity
from secdogie_transport.hybrid_tunnel import (
    TIER_LAN,
    TIER_RELAY,
    TIER_WEBRTC,
    HybridTunnel,
    tier_label,
)
from secdogie_transport.mesh_rtc import MeshNode
from secdogie_transport.pairing import create_admission
from secdogie_transport.star import (
    ROLE_HUB,
    ROLE_SPOKE,
    IdempotentReceiver,
    SequencedFrameQueue,
    StarAuthorityError,
    StarTopology,
)


def test_star_topology_enforces_single_hub_and_spoke_pinning():
    hub_did = "did:key:hub-pc"
    spoke_did = "did:key:spoke-phone"
    foreign_hub = "did:key:rogue-hub"

    hub = StarTopology(hub_did, role=ROLE_HUB)
    assert hub.is_hub
    rec = hub.register_peer(spoke_did, role=ROLE_SPOKE, name="Phone", tier=TIER_LAN)
    assert rec.role == ROLE_SPOKE
    assert hub.can_accept_control_from(spoke_did) is True
    assert hub.can_accept_evolution_from(spoke_did) is False

    # Hub strictly refuses a second active Hub in the same mesh
    with pytest.raises(StarAuthorityError):
        hub.register_peer(foreign_hub, role=ROLE_HUB)

    # Spoke must pin a distinct Hub and refuses any foreign Hub
    with pytest.raises(StarAuthorityError):
        StarTopology(spoke_did, role=ROLE_SPOKE)

    spoke = StarTopology(spoke_did, role=ROLE_SPOKE, hub_did=hub_did)
    assert not spoke.is_hub
    spoke.register_peer(hub_did, role=ROLE_HUB, name="Main PC", tier=TIER_WEBRTC)
    assert spoke.can_accept_control_from(hub_did) is True
    assert spoke.can_accept_control_from(foreign_hub) is False
    assert spoke.can_accept_evolution_from(hub_did) is True
    assert spoke.can_accept_evolution_from(foreign_hub) is False

    with pytest.raises(StarAuthorityError):
        spoke.register_peer(foreign_hub, role=ROLE_HUB)


def test_sequenced_frame_queue_resumes_after_disconnect_and_receiver_is_idempotent():
    q = SequencedFrameQueue(max_buffer=8)
    rx = IdempotentReceiver()

    f1 = q.enqueue({"m": "control", "op": "gate_sign", "id": "a1"})
    f2 = q.enqueue({"m": "control", "op": "gate_sign", "id": "a2"})
    f3 = q.enqueue({"m": "control", "op": "gate_sign", "id": "a3"})
    assert [f1.seq_id, f2.seq_id, f3.seq_id] == [1, 2, 3]
    assert q.pending_count == 3

    # Frame 1 arrives; receiver executes once and acks 1
    is_new, ack_seq = rx.accept(f1.seq_id)
    assert is_new is True and ack_seq == 1
    assert q.ack(ack_seq) == 1
    assert q.pending_count == 2

    # Mobile spoke suspends and reconnects announcing last_ack_seq=1
    resume = q.resume_message(rx.last_delivered_seq)
    assert resume == {"m": "resume", "last_ack_seq": 1}
    replayed = q.replay_after(resume["last_ack_seq"])
    assert [f.seq_id for f in replayed] == [2, 3]

    # Duplicate delivery of seq 1 or seq 2 is rejected without re-execution
    assert rx.accept(1) == (False, 1)
    assert rx.accept(2) == (True, 2)
    assert rx.accept(2) == (False, 2)
    assert rx.accept(3) == (True, 3)
    assert rx.ack_message() == {"m": "ack", "seq_id": 3}
    assert q.ack(3) == 2
    assert q.pending_count == 0


def test_hybrid_tunnel_auto_degrades_lan_to_webrtc_to_e2ee_relay_and_upgrades_back():
    now = [100.0]
    sent: list[tuple[str, int]] = []
    lan_ok = [True]
    webrtc_ok = [True]
    relay_ok = [True]

    def send_lan(peer, frame):
        if lan_ok[0]:
            sent.append((TIER_LAN, frame["seq_id"]))
            return True, 1, ""
        return False, None, "lan unreachable"

    def send_webrtc(peer, frame):
        if webrtc_ok[0]:
            sent.append((TIER_WEBRTC, frame["seq_id"]))
            return True, 28, ""
        return False, None, "ice failed"

    def send_relay(peer, frame):
        if relay_ok[0]:
            sent.append((TIER_RELAY, frame["seq_id"]))
            return True, 85, ""
        return False, None, "relay offline"

    tunnel = HybridTunnel(
        senders={TIER_LAN: send_lan, TIER_WEBRTC: send_webrtc, TIER_RELAY: send_relay},
        probes={
            TIER_LAN: lambda peer: (lan_ok[0], 1 if lan_ok[0] else None),
            TIER_WEBRTC: lambda peer: (webrtc_ok[0], 25 if webrtc_ok[0] else None),
        },
        failure_threshold=2,
        upgrade_cooldown=10.0,
        require_e2ee_on_relay=True,
        clock=lambda: now[0],
    )
    peer = "did:key:phone-1"

    # Starts on Tier 1 (LAN)
    r1 = tunnel.send(peer, {"m": "control", "sig": "ed25519:ok"})
    assert r1.ok and r1.tier == TIER_LAN and r1.rtt_ms == 1

    # LAN drops: 1st failure stays on LAN, 2nd failure auto-degrades to WebRTC and delivers
    lan_ok[0] = False
    r2 = tunnel.send(peer, {"m": "control", "sig": "ed25519:ok"})
    assert not r2.ok and r2.tier == TIER_LAN
    r3 = tunnel.send(peer, {"m": "control", "sig": "ed25519:ok"})
    assert r3.ok and r3.tier == TIER_WEBRTC and r3.degraded_from == TIER_LAN

    # WebRTC drops too: auto-degrades to Tier 3 (E2EE blind relay)
    webrtc_ok[0] = False
    r4 = tunnel.send(peer, {"m": "control", "sig": "ed25519:ok"})
    assert not r4.ok and r4.tier == TIER_WEBRTC

    # Relay strictly refuses unencrypted plaintext frames!
    r_plain = tunnel.send(peer, {"m": "control", "body": "plaintext-only"})
    assert not r_plain.ok and r_plain.tier == TIER_RELAY
    assert "plaintext" in r_plain.error.lower()

    # E2EE / DID-signed frame passes through blind relay
    r5 = tunnel.send(peer, {"m": "control", "ciphertext": "sealed-box", "encrypted": True})
    assert r5.ok and r5.tier == TIER_RELAY

    # Replay all unacked frames since seq 1
    replayed = tunnel.replay_pending(peer, last_ack_seq=1)
    assert any(o.seq_id == r5.seq_id and o.ok for o in replayed)

    # Probe upgrade: first call when LAN is still down stays on relay and sets last_probe,
    # then within cooldown it stays on relay, and once clock advances past cooldown it promotes to LAN
    lan_ok[0] = False
    assert tunnel.probe_upgrade(peer) == TIER_RELAY
    lan_ok[0] = True
    assert tunnel.probe_upgrade(peer) == TIER_RELAY  # inside cooldown
    now[0] += 15.0
    promoted = tunnel.probe_upgrade(peer)
    assert promoted == TIER_LAN
    snap = tunnel.status_snapshot(lang="zh")
    assert snap[peer]["tier"] == TIER_LAN
    assert snap[peer]["tier_label"] == tier_label(TIER_LAN, "zh")


def test_fleet_coordinator_hub_dispatches_and_spoke_holds():
    coordinator_mod = pytest.importorskip("secdogie_fleet.coordinator")
    protocol_mod = pytest.importorskip("secdogie_fleet.protocol")
    Coordinator = coordinator_mod.Coordinator
    Hello = protocol_mod.Hello

    sent: list[tuple[str, object]] = []
    hub_coord = Coordinator(send=lambda did, msg: sent.append((did, msg)), role=ROLE_HUB)
    hub_coord.on_hello(Hello(node_id="did:key:n1", label="Worker"))
    hub_coord.submit("check disk space")
    hub_coord.pump()
    assert len(sent) == 1 and sent[0][0] == "did:key:n1"

    spoke_sent: list[tuple[str, object]] = []
    spoke_coord = Coordinator(send=lambda did, msg: spoke_sent.append((did, msg)), role=ROLE_SPOKE, hub_did="did:key:hub")
    spoke_coord.on_hello(Hello(node_id="did:key:n2", label="Worker"))
    spoke_coord.submit("check disk space")
    spoke_coord.pump()
    assert spoke_sent == []  # Spokes never dispatch tasks unilaterally


def test_mesh_node_snapshot_exposes_star_role_tier_and_unacked():
    owner = Identity.generate()
    device = Identity.generate()
    peer = Identity.generate()
    adm_self = create_admission(owner, "home", device.did, invite_id="1" * 32, name="Hub PC")
    adm_peer = create_admission(owner, "home", peer.did, invite_id="2" * 32, name="Phone")

    node = MeshNode(
        device,
        mesh="home",
        owners=[owner.did],
        admission=adm_self,
        admissions=[adm_peer],
        signal_url="ws://127.0.0.1:8765",
        role=ROLE_HUB,
        default_tier=TIER_LAN,
    )
    snap = node.snapshot()
    assert snap["role"] == ROLE_HUB
    assert len(snap["devices"]) == 1
    d = snap["devices"][0]
    assert d["did"] == peer.did
    assert d["tier"] == TIER_LAN
    assert d["unacked"] == 0
