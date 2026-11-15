# secdogie plan

What we are building, what is decided, where each track stands, and what comes
next. The README says what it is and how to get it; this file says how it gets
finished. Earlier design notes (architecture, audits, per-package docs) live in
the git history before this file replaced them.

## North star

A decentralized, DID-authenticated, supervised companion that runs only on
devices you own. It has no central server and lives on by peer to peer. It
learns from public or authorized sources and perceives by structure, not
pixels. It examines every step with two Socratic gates, and it acts in the
world only on an authenticated local device, under a capability grant, with
you in the loop.

The red lines in the README's safety boundary hold in every slice, and no
slice, bundle or setting can loosen them.

## Decisions

Code comments refer to these by number.

1. **GUI:** PySide6 (`PySide6-Essentials`, no WebEngine), Qt Quick.
2. **WebRTC in Python:** aiortc. Python sends only the `sha-256` DTLS
   fingerprint, the one both ends sign (Chromium keeps only the strongest
   fingerprint of a remote description).
3. **TURN:** none in v1. A symmetric NAT falls back to a relay.
4. **Who signs a policy bundle:** `gate-rules` and `ingest` need one operator;
   `decision-model` and `allowlist` need a k-of-n threshold of masters (the
   same machinery as revocation).
5. **Ingestion politeness:** at most one request per second and one at a time
   per origin; robots.txt cannot be turned off.
6. **Perception:** structure first. Studio and ingestion contain no screen
   capture, and a purity test enforces it. Screenshots in the agent's live
   loop on Windows and Linux are being replaced (track D).
7. **Code signing:** releases ship unsigned, and say so, until there are
   Apple Developer ID and Windows Authenticode certificates.
8. **Lifetime** (revised by the owner, 2026-11; being built). There are two
   modes:
   - **With Studio** (the default): closing the window leaves a light, and
     quitting stops the node.
   - **Always on:** the node keeps running in the background after Studio
     quits, until it is stopped from Studio.

   Starting at login is a visible switch, off by default, shown in Studio
   and in the system's startup items. Nothing else ever starts at login.
9. **Only your own devices** (2026-11). A device joins by pairing: invitation,
   agree, direct WebRTC, six digits on both screens, an admission signed by
   the owner. The public tier (`PeerBook(public=True)`) is kept but off.
10. **Tunnel:** only the C + libsodium implementation in `tunnel/`. Relays
    forward ciphertext only; NAT traversal is coordinated over the mesh, and
    only between allowlisted nodes.
11. **Revocation:** a k-of-n threshold of masters, permanent.
12. **Human in the loop:** low-risk steps may run unattended. High-risk steps
    are confirmed at every entry point, in every mode.
13. **Policy bundles are signed declarative data, never code.** A fixed
    interpreter runs them. They can only tighten: they never touch Gate 2,
    human confirmation, capabilities outside `GRANTABLE_SCOPES`, or the
    forbidden list.
14. **Weighty steps are thought through, never confirmed** (owner, 2026-11).
    The automatic Socratic gate never asks "are you sure?". This covers
    deleting, wiping a disk, accounts in bulk, moving money, saying what
    cannot be unsaid (a message, a post, a resignation), watching someone
    without their knowing, and closing an account.

    It thinks with `citadel/mind/`, a decision model with no training data.
    Its knowledge is written down and every number can be read and argued
    with:
    - **perceive:** the request becomes a frame (the act, the things, the
      people, money, what a message would say). The moment becomes a
      situation (feelings and how strong, the hour, a run of hard days,
      having thought it over, who they are).
    - **Bayes over motives:** priors times likelihood ratios. For example,
      分手 makes "not wanting to see them" likelier, and 安全账户 makes a
      scam likelier.
    - **consequences:** every response is imagined under every motive:
      - what it serves;
      - the regret it risks, which is larger in a hot state and smaller once
        thought over or waited out;
      - the harm to others and to them;
      - the rules it breaks;
      - what overriding them costs, which grows with their autonomy.
    - **expected utility,** leaning toward what can be undone; an argument
      ledger; the motives that would change the answer.
    - **checked offers:** a phrase it offers to change its mind ("已经备份过了")
      is simulated first, and kept only if it really does. A cooling-off is
      never undone by a password.

    Then it goes ahead (Gate 2 still signs), or it dissuades (劝阻) with a
    letter written from its own trace:
    - what it noticed;
    - what the thing is to them;
    - the questions it asked itself;
    - the gentler paths (seal it away, encrypt, back up first, draft now
      and send in the morning, call to verify, talk in person);
    - a cooling-off: 7 days when upset, until the morning for a message,
      3 days when the thing is only still in use;
    - the choice left with them.

    Covertly watching someone is refused, because the harm falls on them.
    Words of not wanting to live come before everything: no task, and the
    letter gives the hotlines (12356, 400-161-9995, 120 / 110). A question
    is kept only for *what* is meant, and only when the value of asking
    exceeds the interruption. Fraud is still refused for good.

    The mind also does three more things:
    - **It learns from the person, never from training data**
      (`mind/history.py`). Their own record is counted:
      - times they came back calm after a cooling-off: their say weighs
        more, and the next cooling-off is shorter (7 → 5 → 3 days);
      - times they undid such a step: regret weighs more;
      - reasons they gave before: they shift that motive's prior.
    - **It weighs every act** in a request and answers for the weightiest.
    - **It knows three more risks:** handing over a code or password (a
      客服 asking for it is a scam, and nothing unlocks it), running what a
      stranger sent, and lending to someone met online.

    Its evaluation is about eighty situations, with metamorphic properties
    (`citadel/tests/test_mind_eval.py`).

    **The optional thinker** (`citadel/thinker.py`) is an LLM the owner may
    plug in with an API key, and it is off without one.
    - It can only add caution: turn going ahead into dissuading, or add a
      reading of distress. It never lowers the gate, and a calm person who
      said they thought it over is not overruled.
    - It sees only the request, the trace and one portrait sentence.
    - Providers: Claude through the official SDK (structured output,
      refusal fallbacks), or any OpenAI-compatible endpoint.
    - The key stays in `<profile>/thinker.json` (0600). Every consultation
      is journaled.
15. **The device in use is the central dispatcher** (owner, 2026-11; being
    built). Among your own devices, the one you are using now takes the
    coordinator's role (the drawing's "central server"). When you leave it,
    the role falls to an always-on device.
## Where each track stands

✅ done · 🔨 under way · 🔜 not started

| Track | What | Status |
| --- | --- | --- |
| A · Network | DIDs, sessions, real P2P UDP, rendezvous, direct-path upgrade, relay, membership gossip, carriers (UDP, WebRTC) | ✅ |
| B · State | Signed hash-chained journal, StateStore, anti-entropy replication across the mesh | ✅ |
| C · Mind | Instruction gate and plan gate in the live loop, intent contracts, known failures, staged memory, the decision model; the mind (`citadel/mind/`) thinks weighty steps through and dissuades rather than confirms (decision 14) | ✅ |
| M · Inference mesh | Protocol v2: three modes (nominal, degraded with central fallback, lockdown) as an explicit state machine, bounded sessions, Merkle-DAG heartbeats (a fork isolates the node), a history-aware decision gate; only an operator signature leaves lockdown | ✅ |
| D · Perception | Fused AX + DIB observation and TOCTOU-safe targets exist as building blocks; not yet in the live loop | 🔨 |
| G · Studio | The desktop app: wallet overview, conversation, pairing and My devices, the Gate page (how the gate thinks, your rules on every device, the thinker), the first meeting, 墨 / 宣纸, 中 / EN, all on the Aesper mist with springy motion | ✅ |
| K · Packaging | PyInstaller builds and installers (setup.exe, .dmg, AppImage, portable zips), self-tested in CI | ✅ (unsigned) |
| P · Policy | Bundle schema, tighten-only validator, golden vectors (Python + TS), local injection, the gate rules spreading to every one of your devices (owner signs once; each node judges; per-device state) | ✅ |
| S · Own devices | Pairing and the mesh in the browser and in Studio (below) | 🔨 |
| I · Ingestion | `WebStateIngestionService`: origin-sharded structured collection | 🔜 |
| T · Tunnel hardening | Noise IK v2 handshake, rekeying, end-to-end relaying, a local control socket | 🔜 |

## Your own devices

How a device joins and stays in touch:
- **Pairing.**
  1. The owner writes an invitation, signed and with an expiry.
  2. The new device reads it offline, then agrees.
  3. The two connect directly over a WebRTC data channel.
  4. Each side signs the DTLS fingerprints it sees, so a man in the middle of
     the signaling is caught.
  5. Both screens show the same six digits; the owner admits the device by
     signing an admission.
- **The mesh.** Each pair of devices meets in its own signaling room. Both
  ends sign a `link-hello` before anything else is read. Then DID-signed
  frames carry the roster, notes and pings. A new device learns the rest of
  the mesh from the owner's roster.
- **Golden vectors** (`fixtures/vectors/pairing.json`, `meshlink.json`) keep
  Python and the browser byte for byte in step.

| Slice | What | Status |
| --- | --- | --- |
| S1 | Pairing protocol: invitation, bind, six digits, admission (Python reference, browser, vectors) | ✅ |
| S2 | `webrtc/pair.html`, bilingual | ✅ |
| S3 | Admissions make devices your own; the public tier is off by default | ✅ |
| S5 | Browser devices online as a mesh (`webrtc/mesh.html`) | ✅ |
| S8a | Python WebRTC (`rtc.py`, `signal_server.py`, `pairing_session.py`, `mesh_rtc.py`), meshing with browsers in both directions | ✅ |
| S11 | Studio's Devices page and wallet | ✅ |
| S4 | The node reads the admissions, so paired devices join its owner list | 🔜 |
| S6 | Revocation: a revoked device's links drop at once and never return; rosters converge | 🔜 |
| S7 | Signaling relayed through the mesh, so online devices can link without the gateway | 🔜 |
| S8b | The node's dialogue over WebRTC; `secdogie-node pair` in a terminal | 🔜 |
| S9 | An always-on device forwards between two that cannot link directly; it can neither read nor alter what it forwards | 🔜 |
| S10 | A DID-authenticated signaling mailbox: unsigned, replayed and impersonating connections are refused | 🔜 |

Open questions for the owner:
- **Where the owner key lives.** Today it is in the first browser that wrote
  an invitation, or in Studio's operator key. The suggestion is to allow
  several owners and keep a key in each.
- **Where the signaling gateway runs.** The suggestion is the owner's own
  Cloudflare account, with the pairing and My devices pages on Pages over
  https.
- **Phone approvals.** Should a phone be able to approve high-risk steps?
  It can be done (the signature is made in the phone's browser). The default
  is to view only.

## Next, in order

0. **Run modes and the dispatcher** (decisions 8 and 15), and the connection
   rules of the owner's drawing on the real mesh. The rules already in
   `inference/`:
   - bypass around a failed node;
   - error → the central server;
   - a standby forwarder for a stranded model node.

   Still to add:
   - with 2 or fewer P2P nodes left, recruit redundant ones;
   - the API key holder's edge cache;
   - a load balancer in front of several dispatchers.

   (The optional thinker, the Gate page, the Aesper redesign of every page
   and the rules on every device are done.)
1. **Close the device loop.**
   - S4: the node trusts paired devices.
   - S6: revocation.
   - S8b: the dialogue over WebRTC, so a phone can talk to the companion.
2. **Spread policy** (P2): the gate rules already travel to every device
   over the WebRTC mesh, and each node judges them. Still to do:
   - other kinds (decision models, allowlists) on the same path;
   - a revoked signer's bundles stop applying.

   Then P3 (`gate-rules` in Gate 1 and the plan gate, Python and TS), P4
   (decision models: shadow, then canary, then full) and P5 (threshold-signed
   allowlists).
3. **Perception in the loop** (D, led by the owner):
   - structure first on Windows (UIA) and macOS (AX hit testing);
   - screenshots only when the owner turns them on.
4. **Mesh runtime:**
   - UDP over rendezvous (T3);
   - live gossip (T4);
   - revocation carried durably through the journal (T6);
   - device classes and headless isolation (T7).
5. **Signaling:** S7 and S10, then S9 forwarding.
6. **Tunnel hardening** (T9).
7. **Ingestion** (I1–I7), starting with:
   - the `ingest/v1` protocol and HRW sharding by origin;
   - the polite fetcher;
   - structured snapshots without images.
8. **Code signing** (K3), when certificates exist.
9. **A multi-node demo:** one goal, learned and acted on by one device, with
   the result converging on the others. Then a security re-review and
   verification on real machines.

## Studio's design

- **A wallet.** The overview is a stack of cards drawn like bank cards:
  - device cards wear a glaze picked from the device's key;
  - the node's card is black, with the day as a dome of dots (each
    quarter-hour you were at the computer lit in that hour's sky);
  - what waits on you turns cinnabar.
- **A Google-style page.** Around the wallet the page follows Google's page
  design: Google Sans, a display greeting, flat tonal sheets, pill buttons,
  and 霁蓝 as the primary colour.
- **The tokens** are in `studio/secdogie_studio/qml/Theme.qml`. Every text
  colour is 4.5:1 or more on its surface.
- **Tests** check that every page draws in both worlds without QML warnings.

## Releasing

1. Merge to `main`.
2. Tag the merge commit `V<major>.<minor>.<patch>` and push the tag.
3. `release.yml` builds everything:
   - the agent;
   - Atlas;
   - the tunnel;
   - Studio for Linux, Windows and macOS (arm64 and x86_64).
4. Every Studio build and installer is installed or mounted on its build
   machine and passes `--self-test` before it is published.

To build without publishing, run the workflow by hand: `publish` is skipped
without a tag.

## How work is done

Each slice follows the same steps:
1. Inspect.
2. Implement.
3. Test: everything stays headless or loopback-testable, signed by a DID and
   gated by an allowlist.
4. Run the full suite.
5. Lint.
6. Commit.

Before a commit, a grep confirms no forbidden primitive has crept in. Gaps are
recorded honestly here, never painted over.
