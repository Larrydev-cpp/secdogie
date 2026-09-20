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
