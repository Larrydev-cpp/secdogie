"""Direct first, relay as the fallback, for one node's application traffic.

`FailoverTransport` sits under a `ChannelMux` (the operator dialogue, say) and
decides per peer which path a message takes:

  * the peer was heard **directly** within ``fresh_for`` seconds -> direct only;
  * otherwise -> direct **and** through a relay both hold a lease with, so the
    message arrives whichever path works. The layers above already tolerate a
    duplicate (the dialogue session acknowledges it again and delivers it once).

Inbound messages from either path reach the same ``deliver`` callback, already
authenticated by the path that carried them: `DirectUDPTransport` for direct
datagrams, `RelayClient.open_relayed` for relayed ones (the inner frame is the
sender's own signed -- and, with transport keys, sealed -- frame; the relay can
neither read a sealed one nor alter any). The relay is addressed by DID
(`RelayClient.route_via`), so two clients of the same relay reach each other
without exchanging membership records.

No new crypto, no traffic obfuscation: this only picks between two paths the
operator configured, like Tailscale falling back to DERP.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Iterable

from .composite import is_link_host
from .membership import ROLE_RELAY, MembershipView, verify_record
from .relay import RelayClient
from .session import Session
from .transport import DeliverFn, Transport
from .udp import DirectUDPTransport

DEFAULT_FRESH_FOR = 6.0  # three dialogue heartbeats
DEFAULT_REFRESH_EVERY = 2.0


class FailoverTransport(Transport):
    """``direct`` is this node's UDP transport; ``relay`` a `RelayClient` over
    it; ``relays`` the relay DIDs to use, in order of preference (the ones this
    node holds a live lease with are tried first)."""

    def __init__(self, direct: DirectUDPTransport, relay: RelayClient, relays: Iterable[str], *,
                 fresh_for: float = DEFAULT_FRESH_FOR, clock=time.monotonic):
        self.direct = direct
        self.relay = relay
        self.relay_dids = list(relays)
        if not self.relay_dids:
            raise ValueError("a failover transport needs at least one relay")
        self._fresh_for = float(fresh_for)
        self._clock = clock
        self._heard: dict[str, float] = {}  # peer DID -> last direct inbound
        self._lock = threading.Lock()
        self._stop: threading.Event | None = None

    @classmethod
    def from_records(cls, direct: DirectUDPTransport, records: Iterable[dict], **kw) -> FailoverTransport:
        """Build the link from the relays' own signed membership records (as
        ``secdogie-relay`` prints them). Handing a record over is the operator's
        decision to use that relay; each must verify and carry the relay role."""
        from secdogie_identity import ALLOW_ANY, Allowlist

        dids = []
        for obj in records:
            rec = verify_record(obj, allowlist=ALLOW_ANY)  # self-signed; the operator chose it
            if rec is None or ROLE_RELAY not in rec.roles:
                raise ValueError("a relay record must be a valid, self-signed record with the relay role")
            dids.append(rec.did)
        trust = Allowlist(set(dids))
        view = MembershipView(allowlist=trust)
        for obj in records:
            view.merge_record(obj)
        return cls(direct, RelayClient(direct, view, allowlist=trust), dids, **kw)

    # -- Transport --------------------------------------------------------------

    def register(self, session: Session, deliver: DeliverFn) -> bool:
        def direct_in(from_did: str, message: bytes) -> None:
            with self._lock:
                self._heard[from_did] = float(self._clock())
            deliver(from_did, message)

        ok = self.direct.register(session, direct_in)
        self.relay.register(session, deliver)
        return ok

    def route(self, from_did: str, to_did: str, message: bytes) -> bool:
        sent = self.direct.route(from_did, to_did, message)
        if self.direct_is_fresh(to_did) or is_link_host(self.direct.endpoint_host(to_did)):
            # A peer on a link (a browser over WebRTC) is never also sent through
            # a relay: the relay path would carry it outside the link's encryption.
            return sent
        return self._via_relay(to_did, message) or sent

    def migrate(self, did: str, endpoint) -> bool:
        return self.direct.migrate(did, endpoint)

    # -- paths -------------------------------------------------------------------

    def direct_is_fresh(self, peer_did: str) -> bool:
        with self._lock:
            last = self._heard.get(peer_did)
        return last is not None and float(self._clock()) - last <= self._fresh_for

    def _via_relay(self, to_did: str, message: bytes) -> bool:
        live = set(self.relay.relays())
        for relay in sorted(self.relay_dids, key=lambda r: r not in live):
            if self.relay.route_via(relay, to_did, message):
                return True
        return False

    # -- lease upkeep ------------------------------------------------------------

    def start(self, every: float = DEFAULT_REFRESH_EVERY) -> threading.Event:
        """Keep the relay leases alive on a daemon thread until stopped."""
        stop = threading.Event()
        self._stop = stop

        def run() -> None:
            while True:
                try:
                    self.relay.refresh()
                except Exception:  # noqa: BLE001 - a failed refresh retries on the next tick
                    pass
                if stop.wait(every):
                    return

        threading.Thread(target=run, daemon=True, name="relay-leases").start()
        return stop

    def close(self) -> None:
        if self._stop is not None:
            self._stop.set()
        self.relay.close()


__all__ = ["FailoverTransport", "DEFAULT_FRESH_FOR", "DEFAULT_REFRESH_EVERY"]
