# secdogie-transport

A **P2P-ready** peer / session / endpoint model. Upper layers (fleet, citadel
sync) depend on an **authenticated peer session** -- a DID bound to a transport
key (via [secdogie-identity](../identity) binding), reachable at migratable
endpoints -- instead of a hub socket.

- **PeerIdentity** is built from a verified DID<->transport binding, so "who is
  this peer" is a cryptographic fact, not an IP.
- **Session** ties a stable `session_id` to a peer; endpoint migration (a NAT
  rebind) changes the active endpoint but never the identity.
- **Transport** is the seam: register an authenticated session, route by DID.
  Today's `HubTransport` is an in-memory hub-and-spoke router (the current
  topology, kept as fallback / rendezvous / relay); a `DirectUDPTransport`
  (Phase 2.10) implements the same interface for true peer-to-peer.

This layer is transport-mechanism-free (no sockets): it is the identity/session/
routing model, backed later by the fleet TCP or the C tunnel.

## Roaming: which frames may move a peer's endpoint

`DirectUDPTransport` learns a peer's address from its authenticated datagrams,
keyed by DID, so a NAT rebind does not look like a new peer. Every frame carries
the sender's monotonic counter under its signature, in both frame formats, and
passes a per-sender replay window. On top of that:

- **A replay never redirects.** A captured frame sent again, from any address,
  is dropped: it is neither delivered twice nor allowed to move the endpoint.
- **Only the newest frame moves the endpoint.** A late datagram sent before a
  rebind is still delivered if it is inside the window, but it cannot drag the
  endpoint back to the old address. The one exception is a peer with no known
  endpoint yet, whose first direct frame is always adopted.
- **Relayed frames never move it.** `open_relayed(frame, sender=...)` runs the
  same checks, refuses a frame signed by anyone but the claimed sender before it
  touches the window, and never adopts an address.

Wire change: signed-only `secdogie/direct/v1` frames now carry a required `ctr`
field. A frame without one, as sent by an older build, is dropped.

## Relay role (2C)

Any allowlisted node -- a home NAS, a VPS, a desktop -- can serve as a relay, and
clients pick among whoever currently does, so losing one relay degrades to
another instead of partitioning the mesh (`relay.py`).

- **Opt-in role.** `RelayService(transport, allowlist=...)` starts serving on a
  node's existing `DirectUDPTransport`; `stop()` withdraws it. The node
  advertises it with `announce(..., roles=["relay"])`.
- **Discovery by membership.** Roles and relay addresses are fields of the
  self-signed membership record: `roles` (what a node offers) and `relays`
  (where it can be reached right now). A gossiping peer cannot add, remove or
  redirect them. `MembershipView.providers("relay")` lists the candidates.
- **Client + failover.** `RelayClient` keeps leases with up to two relays
  (`refresh()` on a timer; advertise what it returns), heartbeats them, and gives
  up on one that stops answering. It is a `Transport`, so it plugs into
  `DirectUpgrader(relay=...)` unchanged: CONNECT coordination and the relay
  fallback now run over the mesh.
- **Opaque to the relay.** It forwards the end-to-end frame the direct path
  would have sent -- DID-signed, and sealed when encryption is on. The receiver
  applies every direct-path check (signature, allowlist, replay window) except
  roaming: a relay's address is never taken as the sender's endpoint.
- **Zero trust.** Explicit allowlist required on both sides, re-checked on every
  forward (a removed or revoked DID stops being relayed at once); registrations
  must be fresh and monotonic; deliveries are accepted only from relays the node
  asked, with `src` equal to the inner signer; per-DID rate limit, size cap,
  bounded client table, no relay chains.

