# SDTP — SecDogie Tunnel Protocol (v1)

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
- Non-goal (v1): multi-peer routing, rekeying of long-lived sessions, roaming
  across multiple source addresses, post-quantum resistance.

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
mac1            u8[16]  crypto_auth over the preceding fields, keyed with
                        BLAKE2b("SDTP-mac1" || R_static_pk)[0:32]
```

`mac1` is not secrecy — it is a cheap proof that the sender has *looked up*
`R`'s public key, filtering random internet noise / naive scanners before R
does any DH math (same purpose as WireGuard's mac1).

R validates: mac1, and `timestamp_ns` is within a 60s window of local time
**and** strictly greater than the last accepted timestamp seen from this
`i_static_pk` (per-peer monotonic counter — the anti-replay for the
handshake itself).

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
unauthenticated peer.

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

## Known limitations (read before relying on this for anything sensitive)

- No session rekeying — a session's keys live as long as the process does.
  Restart both sides periodically for fresh forward secrecy.
- No peer roaming: a session is bound to the source `(ip, port)` of message
  1; if the client's address changes mid-session the tunnel must
  re-handshake.
- Single peer per process in v1 — this is a point-to-point tunnel, not a
  multi-peer mesh.
- Not constant-time-audited beyond what libsodium itself guarantees for its
  primitives; the surrounding C glue has not been reviewed by a third
  party. Use for personal / educational purposes, not as a compliance
  control.
