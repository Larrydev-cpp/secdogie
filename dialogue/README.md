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
| `inspector.py` | Structural tree merge (AX / UIA + DIB metadata, never pixels) and text render model | done |
| `dialogue.py` | Socratic probe / clarification state machine (timeout = fail closed) | done |
| `session.py` | Envelopes over a lossy link: fragmentation, a reliable control channel (ack + backoff, give-up reported), unreliable snapshots, heartbeats; `SessionRouter` binds sessions to a transport `ChannelMux` channel by verified peer DID | done |
| `agent_bridge.py` | The node's end: `OperatorBridge` turns a destructive step into a Gate 2 challenge (the gate verifies the returned token; the signature is the step's confirmation), `ask_user` into a Socratic probe whose answer returns as text, and answers control requests; a vanished peer fails every pending wait | done |
| `publisher.py` | The node's end of the structural view: the element targets the loop offers the model each step become a full tree, then deltas on stable handles (each names its base generation); the App's RESYNC gets the full tree at once. Reads only the named structural fields; a DIB travels as size + format + hash, never pixels | done |
| `app.py` | `AppController`: the App without a screen -- conversation, structural view (asks for a resync on a gap), Gate 2 challenges (reviewed on arrival and again when signing; the operator key is unlocked for one signature), memory offers (id recomputed from the content shown before confirming), control requests; plus the headless script runner | done |
| `tui.py` | Textual split screen over `AppController` (optional `[tui]` extra); node text rendered literally, never as markup; `/approve` acts only on the challenge on screen, after the passphrase prompt | done |
| `cli.py` | `secdogie-dialogue connect` (one node, named by DID, is the whole trust set), `new-operator-key`, `operator-did`; `--headless SCRIPT` for end-to-end tests | done |

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

## Running it

```sh
pip install -e ../identity -e ../citadel -e ../transport -e '.[tui]'
secdogie-dialogue new-operator-key op.keystore        # prints the operator DID for the node's operators list
secdogie-dialogue connect --identity app.key --node did:key:z6Mk... --node-addr 10.0.0.5:7950 \
    --operator-keystore op.keystore
```

The screen: the dialogue on the left, the structural view and the Gate 2 /
memory console on the right, one command line at the bottom. Type an answer
(or an option number) for the oldest open question; `/approve` or `/deny` the
challenge on screen; `/confirm` or `/dismiss` the memory offer on screen;
`/goal <task>`, `/stop|/pause|/resume <goal_id>`, `/retract <memory_id>`,
`/resync`, `/quit`. There is no key that approves: `/approve` shows a
passphrase prompt, and only a challenge the App's own review passed gets one.

Headless (`--headless SCRIPT`, JSON lines, one result line per step, exit 0
only if every step succeeded) is for end-to-end tests. A script approves
nothing by default: each `approve` step names the action (its kind, and a
target id / name or, for a key press, its text) and signs the one challenge
that matches, after the same review.

```json
{"op": "add_goal", "title": "file the report", "goal_id": "g1"}
{"op": "answer", "match": "folder", "text": "Desktop"}
{"op": "approve", "action": {"kind": "delete", "target_name": "report.txt"}}
{"op": "confirm_memory", "key": "report-folder"}
{"op": "expect_status", "match": "done"}
```

## Tests

```sh
pip install -e ../identity -e ../citadel -e ../transport -e '.[tui]' pytest
pytest tests -q
```

All tests are headless: the screen runs under Textual's `run_test()` pilot,
the network is an in-memory wire or UDP on 127.0.0.1.
