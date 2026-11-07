# secdogie-studio

The desktop app for Windows, macOS and Linux. Opening it opens the **home
window**, which has two pages:

- **对话 (conversation):** what you and the companion said, and the one thing
  that waits on you right now, above the line you type into.
- **节点与网格 (node and mesh):**
  - this node: its state, DID, roles, address, how long it has been awake;
  - its work: what it is doing, queued, done;
  - its mesh: your signing key and your App, and other nodes once
    invitations and discovery exist;
  - the gate rules in force, which you can edit and inject right there.

Closing the home window does not quit. It leaves **the companion's presence on
your desktop**: a faint, breathing light in a corner of the screen. Clicking
the light opens the home window again. "让我休息" quits (window and node
together).

While the home window is closed, the light behaves as follows.

- **Quiet by default.** Most of the time the light is all there is. When it has
  something to say in passing ("I've started on …", "I stopped a step: …"), one
  line fades in beside the light and fades out again.
- **It speaks when the work needs you.** A card rises above the light, one at a
  time, for one of these:
  - **A Socratic question (Gate 1).** Something about a goal is unclear or does
    not hold up. You pick an option or answer in your own words.
  - **A signature (Gate 2).** A step cannot be undone. The card shows what the
    signature commits to (action, target, rollback, node, expiry, fingerprint).
    "签名同意" stays disabled for its first 0.8 s. Your passphrase unlocks the
    operator key for that one signature.
  - **A memory.** The companion would like to remember something you told it.
    Keep it or not.
  - **The first meeting** (first run). It sets up its keys and your passphrase.

  The card fades away once you have answered.
- **It waits for a pause.** Attention comes from one coarse signal: the OS's own
  idle counter (`idle.py`). It never sees keys, windows or the screen. While
  you type without pausing, it holds what it has. A few seconds of quiet, or a
  hover over the light, and the card comes up. Anything the work is waiting on
  surfaces after 30 seconds at most. A card never takes the keyboard focus, and
  when nobody is there, nothing surfaces: a signature that expires unanswered is
  a no.
- **Click the light** to open the home window. **Right-click** (or the Menu
  key) for its inner workings:
  - **此刻**: who it is (DIDs), whether it is awake, and what was said;
  - **规则**: the Socratic gates' rules. Draft them in YAML, try them on a
    sentence, then sign with your passphrase and inject them into the node,
    which verifies them itself and tells you which version is in force.

The visual tone is night with a soft glow: deep ink, warm white text, a light
that breathes slower at rest, warmer when something waits for you, and no alarm
red. Qt Quick draws both windows (`secdogie_studio/qml/`); the design system
is in `DESIGN.md`.

## Under the light

- **The node is a child process.** Studio runs `secdogie-node run` (the same
  program a headless machine runs) with its mesh, roles and
  `--exit-on-stdin-eof`. Studio holds the node's stdin. Closing Studio, a crash
  or a kill all close it, and the node stops cleanly on every OS. Nothing is
  installed, started at login, detached or scheduled.
- **One dialogue with its node**, the same DID-authenticated session the
  Dialogue App uses, plus two more channels on it:
  - `policy/v1` for rules;
  - `admin/v1`, a read-only channel the node uses to report its own status
    to the home window.
- **Three keys** are made at the first meeting:
  - the node's own key;
  - Studio's App key, for talking to the node;
  - your operator key, sealed under your passphrase (Argon2id + SecretBox).

  Your operator key is the only one that can authorize a step that cannot be
  undone. No capability is granted at first run.

| File | Contents |
| --- | --- |
| `presence.py` | attention (from idle time) and the stage: what shows, and when (pure, tested) |
| `idle.py` | the OS idle counter: CoreGraphics, `GetLastInputInfo`, X11 screen saver; `None` on Wayland |
| `companion.py` | the dialogue with the node, turned into cards and whispers; rules drafted, tried, signed, injected |
| `app.py` | `Presence` (everything both windows show, without Qt) and the thin Qt bridge |
| `qml/Home.qml` | the home window: the conversation, the node and its mesh, the first hello |
| `qml/Presence.qml` | the light, the whisper, the cards, the inner sheet |
| `qml/Theme.qml` + parts | the tokens (a singleton) and the shared parts (`MomentCard`, `RulesEditor`, `Pill`, `Field`, …) |
| `motion.py` | whether the person asked for less motion |
| `controller.py` / `nodeproc.py` / `profile.py` | the profile, the child node, the keys |
| `link.py` / `selftest.py` | the loopback check and `--self-test` |

## Run

```sh
pip install -e ../identity -e ../transport -e ../citadel -e ../policy -e ../dialogue -e ../node -e '.[gui]'
secdogie-studio                  # the home window; the first meeting asks for your passphrase
secdogie-studio --self-test      # headless: profile, node, link, stop
```

The profile lives in the per-user data directory: `~/Library/Application
Support/secdogie-studio` on macOS, `%APPDATA%\secdogie-studio` on Windows,
`$XDG_DATA_HOME/secdogie-studio` on Linux. A transparent window needs a
compositor on Linux.

## Tests

```sh
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software pytest tests -q
```

- **`test_presence.py`**: holds while you are in flow, surfaces at a pause, and
  a "soon" card surfaces after the maximum hold but never to an empty room. One
  card at a time, never swapped mid-reading. Whispers pass and go stale.
- **`test_companion.py`**: cards in the companion's own words, and a challenge
  that fails the local review cannot be approved. Against a real node, rules
  are injected (a wrong passphrase is refused, a looser rule is refused), and a
  goal the rules question becomes a question and is dropped on "先不做了".
- **`test_app.py`**:
  - the presence wired to a real node: first meeting → keys → "我在。" → rules
    tried and injected → closing stops the node;
  - the real windows offscreen, with no QML warnings allowed:
    - the home window at launch;
    - closing it leaves the light, and clicking the light brings it back;
    - a cold start opens on the first hello;
    - the same with reduced motion.
- **`test_nodeproc.py`**: the node does not outlive a SIGKILLed parent.
- **`test_purity.py`** fails on:
  - screen capture or image processing;
  - reading keystrokes or input events;
  - taking the focus on its own;
  - autostart, services or scheduling;
  - detaching the node.

## Not yet

- Other nodes in the mesh view (invitations, LAN discovery, WebRTC: F1–F4,
  N3), and the decision-model, ingestion and incident pages. They will be
  pages of the home window, never on the light itself.
- Keeping the node after the window closes (tray). Decided in the plan, not
  built yet.
- Installers and signed builds (K1–K3).
