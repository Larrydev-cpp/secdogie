"""A real peer-to-peer UDP transport on the session model (Phase 2.10, brought
forward for the P2P push).

`DirectUDPTransport` implements the same `Transport` interface as `HubTransport`,
but sends datagrams directly between peers over UDP instead of through a hub. Two
deliberate boundaries, so this is an honest advance and not a tunnel rewrite:

  * Authenticity always; confidentiality when a transport key is given. Every
    datagram is a DID-signed frame (secdogie-identity), so a peer cannot be
    spoofed and a frame cannot be tampered with, and delivery is keyed by the
    authenticated signer DID, never by source address.
      - Without `transport_key` (v1 frames): signed plaintext, as before. Run
        inside the C `tunnel/` or WireGuard for encryption.
      - With `transport_key` (v2 frames, sealed.py): content sealed with PyNaCl
        `Box` to the peer's key from its verified DID binding, plus per-peer
        replay protection. Fail closed: a peer without a verified binding gets
        nothing (no plaintext fallback), and inbound v1 frames are dropped. No
        forward secrecy -- see sealed.py for the stated limits.
    Both frame versions carry a signed timestamp `ts` (freshness.py) and a
    per-sender counter `ctr`: a frame outside the clock skew is dropped, and one
    inside it goes through the per-peer `ReplayWindow`, so a captured frame --
    including a hole-punch PROBE / PROBE-ACK -- cannot be replayed.
    No new crypto is implemented here, and there is no traffic obfuscation /
    anti-detection.
  * Roaming by identity. An inbound datagram updates the sender's endpoint
    (source address adoption), keyed by DID -- a NAT rebind does not look like a
    new peer. Only a fresh (in-skew, non-replayed), verified frame can move an
    endpoint, so a captured frame replayed from another address does not
    redirect the peer. `route_to` dials a candidate address without touching the
    current route, so probing never displaces a working path.

The datagram sink is injectable (`Channel`): production uses `UDPChannel` (a real
loopback/UDP socket); this makes the transport fully testable on 127.0.0.1.
"""
from __future__ import annotations

import base64
import json
import socket
import threading
import time
from collections.abc import Callable

from secdogie_identity import Identity, sign_payload, verify_payload

from . import sealed as _sealed
from .endpoint import Endpoint
from .freshness import DEFAULT_MAX_SKEW, is_fresh, now_ms
from .peer import PeerIdentity
from .session import Session
from .transport import DeliverFn, Transport

_FRAME_TYPE = "secdogie/direct/v1"


def _encode_frame(identity: Identity, to_did: str, message: bytes, *,
                  ctr: int | None = None, ts: int | None = None) -> bytes:
    """A signed v1 frame. `ts` (ms) and `ctr` sit inside the signed payload, so
    neither can be changed without breaking the signature."""
    payload = {
        "t": _FRAME_TYPE,
        "from": identity.did,
        "to": to_did,
        "ts": now_ms() if ts is None else int(ts),
        "ctr": time.time_ns() if ctr is None else int(ctr),
        "data": base64.b64encode(message).decode("ascii"),
    }
    return json.dumps(sign_payload(identity, payload)).encode("utf-8")


def _decode_frame(raw: bytes, allowlist, self_did: str, *, now: float | None = None,
                  max_skew: float = DEFAULT_MAX_SKEW) -> tuple[str, int, bytes] | None:
    """Verify a v1 frame addressed to `self_did`: signature, allowlist, and a
    signed `ts` within `max_skew` of `now`. Returns (signer, ctr, message); replay
    checking on `ctr` is the caller's (it keeps the per-peer windows)."""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict) or obj.get("t") != _FRAME_TYPE:
        return None
    ok, signer = verify_payload(obj, allowlist)
    if not ok or obj.get("from") != signer or obj.get("to") != self_did:
        return None
    if not is_fresh(obj.get("ts"), now=time.time() if now is None else now, max_skew=max_skew):
        return None
    ctr = obj.get("ctr")
    if not isinstance(ctr, int) or isinstance(ctr, bool) or ctr < 0:
        return None
    try:
        return signer, ctr, base64.b64decode(obj["data"], validate=True)
    except (KeyError, ValueError, TypeError):
        return None


class UDPChannel:
    """A UDP socket with a background receive loop. Injectable so the transport
    is testable without real sockets if needed."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((host, port))
        self.address = self._sock.getsockname()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, on_datagram: Callable[[bytes, tuple], None]) -> None:
        self._sock.settimeout(0.2)
        self._thread = threading.Thread(
            target=self._recv_loop, args=(on_datagram,), daemon=True, name="udp-recv"
        )
        self._thread.start()

    def _recv_loop(self, on_datagram) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                on_datagram(data, addr)
            except Exception:
                pass  # a bad datagram must never kill the receive loop

    def send(self, host: str, port: int, data: bytes) -> None:
        self._sock.sendto(data, (host, port))

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


class DirectUDPTransport(Transport):
    """One local node's direct transport. `register` sets THIS node's session and
    inbound handler (delivery is to the local node, keyed by the local identity);
    remote peers are reached via endpoints learned from `set_peer_endpoint`,
    `migrate`, or -- the roaming path -- an inbound datagram's source address."""

    def __init__(self, identity: Identity, channel: UDPChannel, *, allowlist=None,
                 transport_key=None, max_skew: float = DEFAULT_MAX_SKEW, clock=time.time):
        self.identity = identity
        # Frame freshness: every outbound frame is stamped from `clock`, and an
        # inbound one is accepted only within `max_skew` seconds of it.
        self.max_skew = max_skew
        self._clock = clock
        self.channel = channel
        self._allowlist = allowlist
        self._inbound: DeliverFn | None = None
        self._local_session: Session | None = None
        self._endpoints: dict[str, tuple[str, int]] = {}
        # Encryption (v2 frames) is on when this node has an X25519 transport key.
        if isinstance(transport_key, str):
            transport_key = _sealed.private_key_from_b64(transport_key)
        self._transport_key = transport_key
        self._boxes: dict = {}                      # peer DID -> nacl Box
        self._key_versions: dict[str, int] = {}     # peer DID -> accepted binding key_version
        self._windows: dict[str, _sealed.ReplayWindow] = {}
        self._ctr = time.time_ns()                  # keeps increasing across restarts
        self._ctr_lock = threading.Lock()
        channel.start(self._on_datagram)

    @property
    def encrypted(self) -> bool:
        return self._transport_key is not None

    def add_peer_binding(self, binding: dict, *, now=None) -> bool:
        """Learn a peer's transport public key from its signed DID binding. The
        binding must verify (signature, validity window, allowlist when one is
        set); an older key_version than one already accepted is refused. Returns
        whether the key was accepted."""
        peer = PeerIdentity.from_binding(binding, allowlist=self._allowlist, now=now)
        if peer is None:
            return False
        version = binding.get("key_version", 0)
        if not isinstance(version, int) or version < self._key_versions.get(peer.did, 0):
            return False
        try:
            peer_pk = _sealed.public_key_from_b64(peer.transport_public_key)
        except ValueError:
            return False
        if self._transport_key is not None:
            self._boxes[peer.did] = _sealed.make_box(self._transport_key, peer_pk)
        self._key_versions[peer.did] = version
        return True

    def register(self, session: Session, deliver: DeliverFn) -> bool:
        """Register the local node's own session + its inbound-message handler.
        `deliver(from_did, message)` is called for every authenticated datagram
        addressed to this node."""
        self._local_session = session
        self._inbound = deliver
        session.established = True
        return True

    def set_peer_endpoint(self, did: str, host: str, port: int) -> None:
        self._endpoints[did] = (host, port)

    def route(self, from_did: str, to_did: str, message: bytes) -> bool:
        ep = self._endpoints.get(to_did)
        if ep is None:
            return False  # nowhere to send yet (need an endpoint or an inbound packet first)
        return self._send(ep, to_did, message)

    def route_to(self, to_did: str, endpoint: Endpoint, message: bytes) -> bool:
        """Send one frame for `to_did` to an explicit `endpoint` WITHOUT changing
        the peer's current route -- used to dial a candidate during a hole-punch
        so an unproven address never displaces a working path."""
        return self._send((endpoint.host, endpoint.port), to_did, message)

    def _send(self, ep: tuple[str, int], to_did: str, message: bytes) -> bool:
        with self._ctr_lock:
            self._ctr += 1
            ctr = self._ctr
        ts = now_ms(self._clock)
        if self.encrypted:
            box = self._boxes.get(to_did)
            if box is None:
                return False  # no verified key for this peer: never fall back to plaintext
            frame = _sealed.seal(self.identity, box, to_did, ctr, message, ts=ts)
        else:
            frame = _encode_frame(self.identity, to_did, message, ctr=ctr, ts=ts)
        self.channel.send(ep[0], ep[1], frame)
        return True

    def migrate(self, did: str, endpoint: Endpoint) -> bool:
        """Record that peer `did` moved to `endpoint` (identity unchanged)."""
        self._endpoints[did] = (endpoint.host, endpoint.port)
        if self._local_session is not None and did == self._local_session.peer.did:
            self._local_session.migrate(endpoint)
        return True

    def _on_datagram(self, raw: bytes, addr: tuple) -> None:
        now = self._clock()
        if self.encrypted:
            opened = _sealed.open_sealed(
                raw, allowlist=self._allowlist, self_did=self.identity.did, box_for=self._boxes.get,
                now=now, max_skew=self.max_skew,
            )
            if opened is None:
                return  # plaintext v1 / unsigned / stale / unknown key / tampered / not for us
        else:
            opened = _decode_frame(raw, self._allowlist, self.identity.did,
                                   now=now, max_skew=self.max_skew)
            if opened is None:
                return  # spoofed / unsigned / stale / unauthorized / not for us -> dropped
        signer, ctr, data = opened
        window = self._windows.setdefault(signer, _sealed.ReplayWindow())
        if not window.accept(ctr):
            return  # replayed or too old: dropped before it can move the endpoint
        # Roaming: adopt the source address for this DID (keyed by identity, not
        # by address), so a peer's NAT rebind keeps working without a re-register.
        self._endpoints[signer] = (addr[0], addr[1])
        if self._inbound is not None:
            self._inbound(signer, data)
