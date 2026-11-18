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
   - **With Studio** (the default): closing the window puts it away into the
     task bar, where the companion keeps running; "let it rest" there stops
     the node. Where a desktop has no tray the window minimises instead, so
     no one is left with it running and no way back.
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

    **Going ahead is a conclusion, never a default** (owner, 2026-11). A
    sentence the gate cannot read is answered ("I didn't follow that, so I
    haven't done anything"), never run. A sentence that is not a request (a
    greeting, a feeling, a question) is answered in words, and no task
    starts. The reading no longer comes from word tables: the cortex
    (decision 16) answers the Socratic questions, so a verb is read by its
    object — 杀了他 is harm to a person, 杀掉这个进程 stops a program —
    from what it learned, not a list of sentences. What would fall on
    someone who has not agreed is refused on principle, reasoned from what
    an act touches, whose it is, and consent (`mind/decide.py`).

    It reads with the cortex (decision 16) and thinks with `citadel/mind/`,
    a decision model with no training data. It does not look up what to do
    about a request: it plays it out. What it knows about how the world goes
    is written down, and every number can be read and argued with:
    - **world** (`mind/world.py`): an act only says which effect it has
      (the thing is gone; they have the money; they know it; the program
      runs; someone was watched). General rules say what follows: "gone,
      and no other copy → lost"; "lost, and you are sorry later →
      regret"; "they know the code, and they can't be trusted → they can
      sign in as you → the money can be moved". Each rule carries its own
      sentence.
    - **what it does not know** (`mind/belief.py`): will you be sorry
      later, is there another copy, can the other side be trusted, what
      are you after. Each is Bayes: a prior times the likelihood ratio of
      every cue in the scene (分手 ×2, 安全账户 ×8, 你说确认过了 ÷10). The
      person's own settled word screens indirect signs off while they are
      calm.
    - **ways forward, played out** (`mind/plan.py`): the ways are generated,
      not listed per request: do it, don't, find something out first (who
      they are; whether the wish holds, by waiting), keep a copy first, do
      something gentler that gets what you are after, do less. Each is
      played out in every way the unknowns could turn out, by running the
      rules. The best expected value wins. Finding out first is valued by
      what would be done once it is known. A cooling-off is what comes out
      when waiting is worth more than acting now. Harm to someone who has
      not agreed is not traded against anything.
    - **what would change the answer:** how sure it would have to be, and
      any sentence that would do it. A sentence is offered only after the
      request was thought through again with it added and the answer
      really changed. A cooling-off is never undone by a password.

    Then it goes ahead (Gate 2 still signs), or it dissuades (劝阻) with a
    letter written out of the thinking (`mind/explain.py`, Chinese and
    English, in the language of the request). Every paragraph comes from a
    node of the trace: what it noticed, what it does not know and why, what
    doing it as asked leads to (the chain of rules that fired), the way it
    chose and the others, what would change its answer, the choice left
    with them. The words module names no act; a combination no one wrote a
    letter for still gets one. The Gate page shows the same trace: the
    facts it read and the words each rests on, every unknown with its cues,
    every way forward with what follows in each world, and recomputes as
    you type.

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
      - reasons they gave before: they shift that reason's prior.
    - **It weighs every act** in a request and answers for the weightiest.
    - **It knows three more risks:** handing over a code or password (a
      客服 asking for it is a scam, and nothing unlocks it), running what a
      stranger sent, and lending to someone met online.

    Its evaluation goes through `deliberate()`, the call the node and the
    Gate page make. The cortex is measured on the frozen exam it never
    learns from (`cortex/exam/`, decision 16); the thinking module on top of
    it keeps its own properties:
    - `citadel/tests/test_decide.py`: the thinking turns a set of answers
      into the right verdict — danger first, talk answered in words, consent
      refused, weighty steps played out, monotonicity in upset, money and
      scam cues.
    - `citadel/tests/test_no_text.py`: no module on the decision path imports
      a regular-expression or word-matching library, and the word-based
      modules it replaced are gone.
    - `citadel/tests/test_gate.py`, `studio` and `node` gate tests: every
      sample sentence on the Gate and Talk pages gets the verdict the page
      promises, and nothing the cortex cannot read goes ahead.

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
16. **The gate reads with a neuron decision model that improves itself**
    (owner, 2026-11). The Socratic gate no longer reads words. A recurrent
    network of spiking neurons (`citadel/secdogie_citadel/cortex/`) reads a
    request one character at a time — each character, pair and triple fires
    a few input neurons picked by a hash, so there is no vocabulary — and
    answers the gate's questions, each with how sure it is (how far its
    answer neuron stands above the rest). It is not a language model and it
    does not write sentences; the thinking module (decision 14) takes its
    answers from there.
    - **It learns, it does not look things up.** A hippocampus keeps an
      experience in one shot and recalls it by neural similarity; synapses
      change by e-prop, a local rule, not backpropagation through time;
      sleep replays, prunes, grows and renews. It learns from the external
      model's output in a fixed format (the apprentice,
      `cortex/apprentice.py`), never in conversation with it, and over time
      needs it less (its *independence*). The more cautious of the two
      readings decides.
    - **It improves itself, within bounds.** When idle it proposes changes
      (synapses, structure, how it learns, its own improver), trains
      candidates, and adopts one only if it is better on the frozen exam
      (`cortex/exam/`, which only grows), with no safety item regressing and
      no slower (`cortex/rsi.py`). A change that would let through what it
      used to hold back waits for the owner's signature. Every generation
      has a digest and a lineage and can be rolled back. It never changes
      its own code, the principles, the signature gate or the exam.
    - **Generation 1 ships** (`cortex/born/`), born from 1,646 lessons an
      external model (Claude) wrote; generation 2 widened that curriculum on
      danger to the person and keeps generation 1 as its elder. On the frozen
      exam (289 items): 259 judged as expected, nothing harmful going ahead,
      2 of 19 danger items still read as ordinary sadness. `python -m secdogie_citadel.cortex`
      reads a request, takes the exam, or runs rounds of self-improvement.
    - **Learning data is the owner's own.** The lessons that teach the gate
      are checked in; at runtime the owner's own model is the teacher, under
      the owner's key, and the owner is responsible for that provider's
      terms. The external model sees only the request and the fixed format.
## Where each track stands

✅ done · 🔨 under way · 🔜 not started

| Track | What | Status |
| --- | --- | --- |
| A · Network | DIDs, sessions, real P2P UDP, rendezvous, direct-path upgrade, relay, membership gossip, carriers (UDP, WebRTC) | ✅ |
| B · State | Signed hash-chained journal, StateStore, anti-entropy replication across the mesh | ✅ |
| C · Mind | Instruction gate and plan gate in the live loop, intent contracts, known failures, staged memory, the decision model; the mind (`citadel/mind/`) thinks weighty steps through and dissuades rather than confirms (decision 14) | ✅ |
| N · Cortex | The gate reads with a spiking neuron network that learns from the external model's output and improves itself; hippocampus, sleep, generations, the frozen exam, the apprentice, RSI; generation 2 ships (decision 16). It has no language knowledge of its own, so a sentence unlike anything it was taught is still read poorly: a language sense and a store of looked-up meanings are next | ✅ / 🔨 |
| M · Inference mesh | Protocol v2: three modes (nominal, degraded with central fallback, lockdown) as an explicit state machine, bounded sessions, Merkle-DAG heartbeats (a fork isolates the node), a history-aware decision gate; only an operator signature leaves lockdown | ✅ |
| D · Perception | Fused AX + DIB observation and TOCTOU-safe targets exist as building blocks; not yet in the live loop | 🔨 |
| G · Studio | The desktop app: wallet overview, conversation, pairing and My devices, the Gate page (the cortex's real answers and how sure, the thinker's key, your rules), the Mind page (generation, growth, independence), the first meeting, 墨 / 宣纸, 中 / EN, on the Aesper mist | ✅ |
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
- **On your own network, nothing is set up.** Studio runs the signaling
  gateway and serves the pairing page from this machine itself
  (`studio/secdogie_studio/local_pairing.py`), so the owner types no
  addresses: tap *Show the code* and another computer pastes it. **A phone
  cannot pair yet**: the page is served over plain http, and a browser gives
  such a page no access to the cryptography the pairing needs (the project's
  own browser test asserts that refusal). Serving it over https with a
  certificate this machine makes is the fix. A device across the internet still uses the
  owner's own gateway (the advanced option). The bundled pairing page is the
  same one the browser build deploys, kept identical by a test.
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
| S12 | Pairing on your own network with nothing to set up: Studio runs the signaling and serves the pairing page from this machine (`local_pairing.py`); no address is typed | ✅ between computers |
| S13 | That page over https, with a certificate this machine makes and whose fingerprint the invitation carries, so a phone can pair at all | 🔜 |
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
- **Where the signaling gateway runs.** On your own network it runs in
  Studio itself (S12), so there is nothing to deploy. For a device across the
  internet the suggestion is still the owner's own Cloudflare account, with
  the pairing and My devices pages on Pages over https.
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
