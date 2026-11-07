# secdogie-studio

The desktop app (G1 in [`docs/PLAN-STUDIO-INGEST.zh.md`](../docs/PLAN-STUDIO-INGEST.zh.md)):
a PySide6 window for Windows, macOS and Linux. It is **one more operator App**,
not a new trust root:

- **First run** creates this machine's profile with three keys, each with one
  job:
  - the **node key** signs what the node does;
  - the **App key** lets Studio talk to the node;
  - the **operator key** authorizes destructive steps. It is the only key that
    can, and it is sealed under a passphrase (Argon2id + SecretBox) and unlocked
    for one signature at a time.

  No capability is granted at first run, so the node refuses every action that
  changes the outside world until the operator grants one.
- **The node is a child process.** Studio runs `secdogie-node run`, the same
  program a headless machine runs, with `--exit-on-stdin-eof`. Studio holds the
  child's standard input. When the window closes, the pipe closes and the node
  stops cleanly. If Studio crashes or is killed, the OS closes the pipe and the
  node stops the same way, on every OS. Nothing is installed, started at login,
  detached or scheduled.
- **The link check** opens the same DID-authenticated dialogue the Dialogue App
  uses (`secdogie_dialogue`) over loopback and makes one round trip that
  changes nothing: it asks the node to retract a memory that does not exist,
  and the node refuses. That refusal proves the whole path: the transport,
  both DIDs and their allowlists, the dialogue session and the node's request
  handling.

The window is Chinese; logs and identifiers are English. Pages for later
slices are already in the navigation, and each names the slice that will build
it.

## Run

```sh
pip install -e ../identity -e ../transport -e ../citadel -e ../dialogue -e ../node -e '.[gui]'
secdogie-studio                          # the window; first run asks for the operator passphrase
secdogie-studio --profile /path/to/dir   # another profile
secdogie-studio --self-test              # headless: profile, node, link, stop (JSON lines, exit 0)
```

The profile lives in the per-user data directory:

| OS | Directory |
| --- | --- |
| macOS | `~/Library/Application Support/secdogie-studio` |
| Windows | `%APPDATA%\secdogie-studio` |
| Linux | `$XDG_DATA_HOME/secdogie-studio` (default `~/.local/share/secdogie-studio`) |

Profile files are never overwritten, not even keys left behind by an
interrupted first run. On a machine without a display the command says so and
points to `secdogie-node` and `secdogie-dialogue` instead.

## Layout

| File | Contents |
| --- | --- |
| `profile.py` | the profile and first run (keys, allowlists, settings) |
| `nodeproc.py` | `NodeProcess`: the child node, its ready line, its log, stopping |
| `link.py` | the loopback dialogue round trip |
| `controller.py` | what the window does, without Qt: workers, node states, snapshots |
| `viewmodel.py` | labels, rows and validation, without Qt |
| `app.py` | the PySide6 window, a thin view that polls `snapshot()` |
| `selftest.py` | `--self-test` |
| `cli.py` | `secdogie-studio`, including `--node-child` for frozen builds |

## Tests

```sh
pip install pytest
QT_QPA_PLATFORM=offscreen pytest tests -q
```

- **Node lifetime.** `test_nodeproc.py` SIGKILLs Studio's process and checks
  that the node stops by itself.
- **Window.** `test_gui.py` drives the real window offscreen: it fills in the
  first-run form, starts the node, opens a planned page, and checks that closing
  the window stops the node.
- **Red lines.** `test_purity.py` fails on any screen capture or image
  processing, on anything that autostarts, runs as a service or schedules
  itself, and on any attempt to detach the node from the window.

On a bare Linux box Qt needs `libegl1 libgl1 libxkbcommon0 libfontconfig1`.
Without PySide6 the GUI test is skipped and everything else still runs.

## Not yet

- **G2.** The dialogue page: goals, Socratic questions, Gate 2 signing,
  memory confirmation.
- **G3 to G6.** The mesh, discovery, rules, decision-model, ingestion,
  incidents and keys pages.
- **Tray.** Keeping the node after the window closes. Decided in the plan: off
  by default, visible in the tray, not built yet.
- **K1 to K3.** Installers and signed builds.
