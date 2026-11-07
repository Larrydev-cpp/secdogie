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
        inside the C `tunnel/` for encryption.
      - With `transport_key` (v2 frames, sealed.py): content sealed with PyNaCl
        `Box` to the peer's key from its verified DID binding, plus per-peer
        replay protection. Fail closed: a peer without a verified binding gets
        nothing (no plaintext fallback), and inbound v1 frames are dropped. No
        forward secrecy -- see sealed.py for the stated limits.
    No new crypto is implemented here, and there is no traffic obfuscation /
    anti-detection.
  * Roaming by identity. An inbound datagram updates the sender's endpoint
    (source address adoption), keyed by DID -- a NAT rebind does not look like a
    new peer. In both modes every frame carries the sender's monotonic counter
    under its signature and passes a per-sender replay window, and only the
    NEWEST authenticated frame may move the endpoint:
      - a captured frame replayed from another address is dropped, so it
        neither re-delivers nor redirects the peer;
      - a late frame sent before a NAT rebind is still delivered but cannot
        drag the endpoint back to the old address;
      - a frame that arrived through a relay never moves the endpoint at all
        (`open_relayed`), though it does advance the window.

The pipe is a `Carrier` (carrier.py, N0): production uses `UDPChannel` (a real
UDP socket), and a `CarrierSet` puts several carriers under one transport. Every
check above lives here, above the carrier, so it holds whichever pipe a frame
came on. With a `CarrierSet`, an endpoint remembers its carrier: a reply goes
back on the carrier the newest authenticated frame arrived on, and the transport
also keeps the last good address per carrier (`peer_endpoints`) for carrier
selection to build on. Roles that share the socket (`on_frame`: relay,
rendezvous) are served on the primary carrier only.

Other protocols can share the socket: `on_frame(type, handler)` hands inbound
envelopes of that type to a handler instead of the direct-message path (the relay
role in relay.py uses this), and `open_relayed` accepts an end-to-end frame that
arrived through a relay with every direct-path check except roaming.
"""
from __future__ import annotations

import base64
import json
import socket
import threading
import time
from collections.abc import Callable

from secdogie_identity import Identity, require_trust, sign_payload, verify_payload

from . import sealed as _sealed
from .carrier import Carrier
from .endpoint import Endpoint
from .peer import PeerIdentity
from .session import Session
from .transport import DeliverFn, Transport

_FRAME_TYPE = "secdogie/direct/v1"

FrameHandler = Callable[[dict, tuple], None]  # (parsed envelope, source address) -> None


def _encode_frame(identity: Identity, to_did: str, message: bytes, ctr: int) -> bytes:
    payload = {
        "t": _FRAME_TYPE,
        "from": identity.did,
        "to": to_did,
        "ctr": ctr,  # signed, so it cannot be stripped or rewritten; feeds the replay window
        "data": base64.b64encode(message).decode("ascii"),
    }
    return json.dumps(sign_payload(identity, payload)).encode("utf-8")


def _parse(raw: bytes) -> dict | None:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _decode_frame(raw: bytes, allowlist, self_did: str) -> tuple[str, int, bytes] | None:
    obj = _parse(raw)
    if obj is None or obj.get("t") != _FRAME_TYPE:
        return None
    ok, signer = verify_payload(obj, allowlist)
    if not ok or obj.get("from") != signer or obj.get("to") != self_did:
        return None
    ctr = obj.get("ctr")
    if not isinstance(ctr, int) or isinstance(ctr, bool) or ctr < 0:
        return None  # no counter (an older build's frame) -> cannot be replay-checked -> dropped
    try:
        return signer, ctr, base64.b64decode(obj["data"], validate=True)
    except (KeyError, ValueError, TypeError):
        return None


class UDPChannel(Carrier):
    """A UDP socket with a background receive loop: the `udp` carrier."""

    kind = "udp"
    reliable = False
    max_frame = 65507

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

    def send(self, host: str, port: int, data: bytes, carrier: str | None = None) -> None:
        self._check_kind(carrier)
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
    `migrate`, or -- the roaming path -- an inbound datagram's source address.

    The name predates the carrier seam: `channel` is any `Carrier`, a single
    `UDPChannel` or a `CarrierSet`. `DirectTransport` is the same class."""

    def __init__(self, identity: Identity, channel: Carrier, *, allowlist=None,
                 transport_key=None):
        self.identity = identity
        self.channel = channel
        self._allowlist = require_trust(allowlist, "DirectUDPTransport")
        self._inbound: DeliverFn | None = None
        self._local_session: Session | None = None
        # did -> where `route` sends: (host, port) on the primary carrier, or
        # (host, port, kind) when the address came from a named carrier.
        self._endpoints: dict[str, tuple] = {}
        # did -> kind -> (host, port): the last good address on each carrier.
        self._carrier_endpoints: dict[str, dict[str, tuple[str, int]]] = {}
        self._primary_kind = getattr(channel, "primary_kind", None) or "udp"
        # Encryption (v2 frames) is on when this node has an X25519 transport key.
        if isinstance(transport_key, str):
            transport_key = _sealed.private_key_from_b64(transport_key)
        self._transport_key = transport_key
        self._boxes: dict = {}                      # peer DID -> nacl Box
        self._key_versions: dict[str, int] = {}     # peer DID -> accepted binding key_version
        self._windows: dict[str, _sealed.ReplayWindow] = {}
        self._ctr = time.time_ns()                  # keeps increasing across restarts
        self._ctr_lock = threading.Lock()
        self._handlers: dict[str, FrameHandler] = {}  # envelope type -> handler (e.g. relay role)
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

    def set_peer_endpoint(self, did: str, host: str, port: int, *, carrier: str | None = None) -> None:
        """Send to `did` at (host, port), on `carrier` (a kind) or the primary one."""
        self._endpoints[did] = (host, port) if carrier is None else (host, port, carrier)
        self._carrier_endpoints.setdefault(did, {})[carrier or self._primary_kind] = (host, port)

    def peer_endpoints(self, did: str) -> dict[str, tuple[str, int]]:
        """The last good address of `did` on each carrier kind, as learned from
        its authenticated frames or set explicitly. A copy."""
        return dict(self._carrier_endpoints.get(did, {}))

    def peer_carrier(self, did: str) -> str | None:
        """The carrier kind `route` currently uses for `did`, or None."""
        ep = self._endpoints.get(did)
        if ep is None:
            return None
        return ep[2] if len(ep) > 2 else self._primary_kind

    def on_frame(self, frame_type: str, handler: FrameHandler | None) -> None:
        """Hand inbound envelopes whose `t` is `frame_type` to `handler(obj,
        addr)` instead of the direct-message path; `None` removes the handler.
        The handler authenticates the envelope itself. The direct frame types
        cannot be taken over."""
        if frame_type in (_FRAME_TYPE, _sealed.SEALED_TYPE):
            raise ValueError(f"{frame_type!r} is the direct-message frame type")
        if handler is None:
            self._handlers.pop(frame_type, None)
        else:
            self._handlers[frame_type] = handler

    def build_frame(self, to_did: str, message: bytes) -> bytes | None:
        """The end-to-end frame `route` sends to `to_did`: DID-signed and carrying
        the next counter, and sealed to the peer's bound key when encryption is
        on. None when encryption is on and the peer has no verified key -- never
        a plaintext fallback."""
        box = None
        if self.encrypted:
            box = self._boxes.get(to_did)
            if box is None:
                return None
        with self._ctr_lock:
            self._ctr += 1
            ctr = self._ctr
        if box is None:
            return _encode_frame(self.identity, to_did, message, ctr)
        return _sealed.seal(self.identity, box, to_did, ctr, message)

    def route(self, from_did: str, to_did: str, message: bytes) -> bool:
        ep = self._endpoints.get(to_did)
        if ep is None:
            return False  # nowhere to send yet (need an endpoint or an inbound packet first)
        kind = ep[2] if len(ep) > 2 else None
        if kind is not None and kind not in self.channel.kinds:
            return False  # that carrier is not on this node
        frame = self.build_frame(to_did, message)
        if frame is None:
            return False  # no verified key for this peer: never fall back to plaintext
        if kind is None:
            self.channel.send(ep[0], ep[1], frame)
        else:
            self.channel.send(ep[0], ep[1], frame, carrier=kind)
        return True

    def migrate(self, did: str, endpoint: Endpoint) -> bool:
        """Record that peer `did` moved to `endpoint` (identity unchanged)."""
        self._endpoints[did] = (endpoint.host, endpoint.port)
        self._carrier_endpoints.setdefault(did, {})[self._primary_kind] = (endpoint.host, endpoint.port)
        if self._local_session is not None and did == self._local_session.peer.did:
            self._local_session.migrate(endpoint)
        return True

    def open_relayed(self, frame: bytes, *, sender: str) -> tuple[str, bytes] | None:
        """Authenticate (and decrypt) an end-to-end frame that reached this node
        through a relay, which says it came from `sender`. Same checks as a
        direct datagram -- signature, allowlist, addressed to us, sealed key,
        replay window -- but the relay's address is never adopted as the
        sender's endpoint. A frame signed by anyone but `sender` is refused
        before it touches the replay window, so a relabelled copy cannot use up
        the genuine frame's counter. Returns (signer, message) or None."""
        if not isinstance(sender, str) or not sender:
            return None  # no claimed sender: nothing to hold the frame to
        opened = self._open(frame, expect=sender)
        return None if opened is None else (opened[0], opened[1])

    def _open(self, raw: bytes, *, expect: str | None = None) -> tuple[str, bytes, bool] | None:
        """(signer, message, newest) for an authentic, fresh frame (signed by
        `expect`, when given), else None. `newest` is whether the frame raised
        the sender's highest counter -- the only kind of frame allowed to move
        the sender's endpoint."""
        if not self.encrypted:
            # spoofed / unsigned / unauthorized / not for us / no counter -> None
            opened = _decode_frame(raw, self._allowlist, self.identity.did)
        else:
            # plaintext v1 / unsigned / unknown key / tampered / not for us -> None
            opened = _sealed.open_sealed(
                raw, allowlist=self._allowlist, self_did=self.identity.did, box_for=self._boxes.get,
            )
        if opened is None:
            return None
        signer, ctr, data = opened
        if expect is not None and signer != expect:
            return None
        window = self._windows.setdefault(signer, _sealed.ReplayWindow())
        before = window.highest
        if not window.accept(ctr):
            return None  # replayed or too old: dropped before it can move the endpoint
        return signer, data, before is None or ctr > before

    def _on_datagram(self, raw: bytes, addr: tuple) -> None:
        kind = addr[2] if len(addr) > 2 else None  # tagged by a CarrierSet
        if self._handlers and (kind is None or kind == self._primary_kind):
            # Shared-socket roles (relay, rendezvous) answer on the primary
            # carrier only; their frames arriving on another carrier are not
            # theirs to see, and fall through to the direct path (which drops
            # them: they are not direct frames).
            obj = _parse(raw)
            handler = self._handlers.get(obj.get("t")) if obj is not None else None
            if handler is not None:
                handler(obj, (addr[0], addr[1]))
                return
        opened = self._open(raw)
        if opened is None:
            return
        signer, data, newest = opened
        # Roaming: adopt the source address for this DID (keyed by identity, not
        # by address), so a peer's NAT rebind keeps working without a re-register.
        # Only the newest frame moves it (or the first one, when there is nothing
        # to move yet): a late frame from before the rebind must not drag it back.
        # The carrier travels with the address, so replies go back the way the
        # newest frame came.
        if newest or signer not in self._endpoints:
            self._endpoints[signer] = (addr[0], addr[1]) if kind is None else (addr[0], addr[1], kind)
        per_carrier = self._carrier_endpoints.setdefault(signer, {})
        k = kind or self._primary_kind
        if newest or k not in per_carrier:
            per_carrier[k] = (addr[0], addr[1])
        if self._inbound is not None:
            self._inbound(signer, data)


DirectTransport = DirectUDPTransport  # carrier-neutral name (N0)
