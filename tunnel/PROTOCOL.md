# SDTP — SecDogie Tunnel Protocol (v2)

A minimal, from-scratch, single-peer encrypted UDP tunnel, written in C on
top of vetted primitives from libsodium (X25519, BLAKE2b, XChaCha20-Poly1305).
It is intentionally small enough to read end to end and learn from — it is
**not** an independently audited protocol. Treat it as a personal / educational
VPN, not as a replacement for WireGuard or IPsec in production.

## Goals / non-goals

- Goal: confidentiality + integrity + mutual authentication + forward secrecy
  for a single client <-> server tunnel, carrying arbitrary IP traffic over a
  TUN device.
- Goal: small, auditable C codebase (a few hundred lines), no custom crypto
  primitives — only composition of libsodium primitives.
- Now implemented (beyond the original single-tunnel v1): **multi-peer routing**
  via the optional hub mode (`hub.c`), and **roaming across source addresses** —
  the point-to-point server/client and the hub all adopt a peer's latest source
  address on each authenticated packet (keyed by the 8-byte session id, so a
  session survives a NAT rebind). The hub enforces **cryptokey routing**: a
  decrypted packet's inner source IP must equal the sending peer's tunnel IP, so
  an authenticated peer cannot spoof another's address (`hub.c`,
  `sdtp_hub_parse_ipv4_src`).
- Point-to-point mode enforces the same cryptokey routing when `peer_address =
  <peer tunnel IP>` is set in the config (a warning is printed when it is not).
- Still non-goals: rekeying of long-lived sessions (keys live for the process;
  liveness drives a full re-handshake), NAT hole punching / traffic obfuscation,
  IPv6, and post-quantum resistance.

## Identity

Each peer has a long-term X25519 keypair (`static_pk` / `static_sk`),
generated with `secdogie-tunnel genkey`. Public keys are exchanged out of
band and hard-coded into each side's config, exactly like WireGuard peer
keys — SDTP has no PKI / CA / trust-on-first-use.

## Handshake (1-RTT, mutually authenticated, forward-secret)

This is a "triple-DH" construction (the same idea used by Signal's X3DH and
Noise's `IK` pattern), built directly from libsodium's `crypto_scalarmult`
(X25519) and `crypto_generichash` (BLAKE2b) rather than a full Noise
implementation.

Notation: `I` = initiator, `R` = responder. `I` knows `R_static_pk` ahead of
time (configured). `I` generates a fresh ephemeral keypair per session.

**Message 1 (I -> R), all fields plaintext except where noted:**

```
type            u8      = 1
session_id      u8[8]   random, chosen by I
i_static_pk     u8[32]
i_eph_pk        u8[32]
timestamp_ns    u64     big-endian, wall clock
mac1            u8[16]  keyed BLAKE2b-128 over the preceding fields
                        (libsodium crypto_generichash, 16-byte output), keyed
                        with mac1_key (v2, below) -- NOT libsodium crypto_auth
```

```
ss       = X25519(i_static_sk, R_static_pk)   // == X25519(R_static_sk, i_static_pk)
mac1_key = BLAKE2b-256("SDTP-v2-mac1" || ss || i_static_pk || R_static_pk)
```

`mac1` (v2) authenticates message 1: `ss` is the static-static secret, which only
a holder of `i_static_sk` (or `R_static_sk`) can compute. Public keys are not
secret, so this matters: in v1 `mac1` was keyed by `R_static_pk` alone, which let
anyone who knew both public keys forge a message 1 with a fresh (even
future-dated) timestamp. R accepted it, replaced the live session with one the
forger could not use, re-pointed the peer address, and advanced the replay guard
up to 60s -- one forged datagram cut a working tunnel. v2 peers do not
interoperate with v1 peers (the handshake simply fails; upgrade both sides).

R validates, in order: that the initiator's `i_static_pk` byte-equals a
configured, expected peer static key (a cheap filter -- a hub only pays for the
next step on the matching peer); `mac1` under `mac1_key` (the authentication
gate: one DH, before any state is touched); and that `timestamp_ns` is
within a 60s window of local time **and** strictly greater than the last accepted
timestamp seen from this `i_static_pk` (per-peer monotonic counter — the
anti-replay for the handshake itself).

R then generates a fresh ephemeral keypair and computes three DH shared
secrets:

```
dh1 = X25519(R_static_sk,  i_eph_pk)     // == X25519(i_eph_sk, R_static_pk)
dh2 = X25519(R_eph_sk,     i_static_pk)  // == X25519(i_static_sk, R_eph_pk)
dh3 = X25519(R_eph_sk,     i_eph_pk)     // == X25519(i_eph_sk, R_eph_pk)
```

`dh1` authenticates R to I (only the real R holds `R_static_sk`). `dh2`
authenticates I to R (only the real I holds `i_static_sk`). `dh3` is a
fresh, ephemeral-ephemeral secret that gives forward secrecy: recording all
static keys is not enough to reconstruct traffic keys.

Chaining key:

```
ck0 = BLAKE2b-256("SDTP-v1-chaining-key")
ck1 = BLAKE2b-256(ck0 || dh1)
ck2 = BLAKE2b-256(ck1 || dh2)
ck3 = BLAKE2b-256(ck2 || dh3)

key_i2r = BLAKE2b-256(ck3 || 0x01 || i_static_pk || R_static_pk)
key_r2i = BLAKE2b-256(ck3 || 0x02 || i_static_pk || R_static_pk)
```

**Message 2 (R -> I):**

```
type            u8    = 2
session_id      u8[8] echoed from message 1
r_eph_pk        u8[32]
confirm         u8[16 + 16]  XChaCha20-Poly1305(key_r2i, nonce=CONFIRM_NONCE,
                             aad = type||session_id||r_eph_pk,
                             plaintext="SDTP-HELLO-R2Iv1")
```

I computes the same three DH values (it can: it holds `i_eph_sk`,
`i_static_sk`, knows `R_static_pk`, and now has `r_eph_pk`), derives
`key_i2r` / `key_r2i`, and decrypts `confirm`. Successful decryption is
**implicit proof R holds `R_static_sk`** and completes mutual
authentication; I now trusts the session.

`key_r2i` is reused for the data channel after the handshake, so the
confirmation message's nonce must never collide with a data-channel nonce
under the same key. Data-channel nonces (below) always have their first 16
bytes zeroed, with only the trailing 8 bytes (the counter) varying.
`CONFIRM_NONCE` is fixed to `0x01` followed by 23 zero bytes — its first
byte is never `0x00`, so by construction it can never equal any
data-channel nonce, regardless of counter value.

If anything fails validation (bad mac1, stale timestamp, failed AEAD
decrypt), the message is silently dropped — no error is sent back to an
unauthenticated peer. (The log line for a rejected message 1 is rate-limited to
one per second.)

### Pending sessions and confirmation (`peer_state.c`)

A session R derives from message 1 is **pending**: R does not send with it and
does not move the peer's address to message 1's source. It becomes the
**current** session when the first data/keepalive packet decrypts under it --
proof that I derived the same keys. I sends a keepalive immediately after
accepting message 2, so confirmation costs one round trip. The session it
replaces is kept as receive-only **previous**, so packets already in flight on
the old keys are not dropped at the switch (the WireGuard current/next/previous
pattern). The peer's address is adopted only from an authenticated packet on the
current or newly promoted session, never from a handshake message or a late
previous-session packet.

## Data channel

Once the handshake completes both sides hold a pair of directional keys
(`key_i2r`, `key_r2i`). Each IP packet read off the TUN device becomes one
UDP datagram:

```
type            u8    = 3
session_id      u8[8]
counter         u64   big-endian, strictly increasing per sender, per session
ciphertext      XChaCha20-Poly1305(key, nonce = 16 zero bytes || counter,
                                   aad = type || session_id || counter,
                                   plaintext = raw IP packet from TUN)
```

The counter can never repeat for a given key (it is only ever incremented,
never reset), so nonce reuse is structurally impossible short of sending
2^64 packets on one session.

**Replay protection:** the receiver tracks the highest counter accepted and
a 2048-entry sliding bitmap (same shape as WireGuard's), rejecting anything
already-seen or too far behind the window. Packets that decrypt but fail
the replay check are dropped without effect.

**Keepalive:** `type = 4`, empty ciphertext, sent every 25s of otherwise-idle
traffic to keep NAT/firewall UDP mappings alive. Ignored on receipt beyond
updating the "last seen" timestamp used for peer liveness.

## Hub mode (one node terminating many tunnels)

The wire protocol is point-to-point, but a single node can terminate many of
these tunnels at once and route between them — see `hub` in the README. This
needs **no protocol change**; it reuses fields already on the wire:

- **Handshake demux.** Message 1 authenticates the initiator's static key. A
  hub configured with several client keys simply tries `handshake_respond` with
  each until one authenticates; that identifies the client. (A wrong key fails
  cleanly, leaving no state — same check `test_wrong_peer_rejected` covers.)
- **Data demux.** Every data/keepalive datagram already carries the 8-byte
  `session_id`. The hub keeps one session per client and looks up the right one
  by that id, so each client's counter/replay window stays independent.
- **Routing.** The hub decrypts an inner IP packet, reads its destination
  address, and either writes it to its own TUN (destined for the hub or beyond)
  or re-encrypts it to the client that owns that tunnel IP (client-to-client).

Because the hub decrypts in order to route, **it can see inter-client traffic**
— a hub-and-spoke topology, not end-to-end encryption between clients. Each
client↔hub hop still has the full confidentiality/authenticity/replay
guarantees above.

## Known limitations (read before relying on this for anything sensitive)

- No session rekeying — a session's keys live as long as the process does.
  Restart both sides periodically for fresh forward secrecy.
- Peer roaming IS supported: all three modes (point-to-point server and client,
  and the hub) adopt a peer's latest source address on each *authenticated*
  data/keepalive packet, keyed by the session id — so a NAT rebind is tolerated
  without a re-handshake. There is no roaming rate limit / hysteresis: the last
  authenticated packet wins.
- Point-to-point per session — `server`/`client` carry one peer each. A `hub`
  terminates many client tunnels, but it is a decrypting hub-and-spoke node,
  not a mesh and not end-to-end between clients.
- Not constant-time-audited beyond what libsodium itself guarantees for its
  primitives; the surrounding C glue has not been reviewed by a third
  party. Use for personal / educational purposes, not as a compliance
  control.
