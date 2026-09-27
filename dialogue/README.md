# secdogie-dialogue

The operator's **Dialogue & Alignment App**: where an operator and an Agent node
align on intent, watch the same structural view of the screen, and where a
destructive action gets its Gate 2 operator signature.

It is a consumer, a signer and a dialogue peer. It renders the `Observation`
the Agent sends; it never captures the screen, never reads process memory, and
never talks to a central server. The operator's signing key stays on the
operator's machine; the Agent node never holds it.

Design: [DESIGN.zh.md](DESIGN.zh.md).

## Modules

| Module | What it does | Status |
| --- | --- | --- |
| `protocol.py` | Wire dataclasses, signed envelopes (`seal` / `open_envelope`), replay guard | done |
| `guard.py` / `keystore.py` | Gate 2 operator client: encrypted operator key, challenge review, signed response | done |
| `inspector.py` | Structural tree merge (AX / UIA + DIB metadata, never pixels) and text render model | planned |
| `dialogue.py` | Socratic probe / clarification state machine (timeout = fail closed) | planned |
| `session.py` | Binds the protocol to the transport (Tunnel / relay / WebRTC signaling) | planned |
| `app.py` | Textual TUI over the pure models above | planned |

## The wire, in one paragraph

Every packet is a `{header, kind, payload}` object signed with
`secdogie_identity.sign_payload`. `open_envelope` authenticates first -- the
signer must be on the trust policy (a `TrustPolicy`, so a revoked DID is refused)
-- and only then parses, strictly: all fields required, unknown fields refused,
exact types. The header binds the packet to one recipient, a session and a
sequence number; a packet outside the ±30 s clock window, replayed, or addressed
to another node is dropped without a reply.

## Two keys

The App holds two Ed25519 keys. The **session key** signs every envelope and
stays unlocked while connected; a node lists it as a session peer. The
**operator key** signs Gate 2 authorizations only: it lives in a
passphrase-encrypted keystore (Argon2id + SecretBox, `keystore.py`), is unlocked
when the operator presses Approve, and is dropped after that one signature; a
node lists it as an operator. A stolen session key can talk to a node but cannot
authorize a destructive action.

Before offering Approve, `guard.review_challenge` recomputes the action hash
locally from the action it displays, checks the challenge names the node this
session is authenticated with, and checks it has not expired. Any mismatch and
the App refuses to sign.

## Tests

```sh
pip install -e ../identity -e ../citadel -e . pytest
pytest tests -q
```

All tests are headless: no screen, no network, no TUI.
