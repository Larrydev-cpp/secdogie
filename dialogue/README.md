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
| `guard.py` / `keystore.py` | Gate 2 operator client: local key, challenge review, signed response | next |
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

## Tests

```sh
pip install -e ../identity -e ../citadel -e . pytest
pytest tests -q
```

All tests are headless: no screen, no network, no TUI.
