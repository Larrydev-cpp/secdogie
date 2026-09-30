# secdogie-app

The one secdogie window. Open it, paste an API key the first time, and do
everything else in one conversation:

- **Say what you want.** Type a goal and press Enter. What the node says about
  it appears in the conversation.
- **Answer questions.** When the agent is unsure, it asks. The question appears
  as a card: click a suggested answer, or type your answer in the box at the
  bottom.
- **Approve or deny high-risk steps (Gate 2).** A high-risk step waits for you
  as an approval card. It shows what the step would do, why it is dangerous,
  and whether the action hash matches when it is recomputed on your machine.
  - To approve, enter your passphrase. The first time, you set the passphrase
    here and type it twice.
  - Deny it, or let it expire, and the node refuses the step.
- **Confirm memory.** A note the model wants to keep appears as a card:
  Remember it, or skip it. A skipped note stays in quarantine until it expires.
- **Stop.** Stops every goal you sent that has not finished.
- **API key.** The key button in the corner changes the key.

```
pip install -e ../identity -e ../transport -e ../citadel -e ../dialogue -e ../node -e ../agent -e .
secdogie            # or: python -m secdogie_app
```

## What runs

The window starts the ordinary resident node (`secdogie-node`) inside the same
process, listening only on 127.0.0.1. The window and the node are wired exactly
as a remote App and node are:

- each has its own key;
- they speak the signed dialogue over a DID-authenticated UDP transport;
- each has an allowlist naming the other.

Being local loosens no check. The window itself (`model.py`, `window.py`) only
shows what the dialogue App's controller holds and calls it. Gate 1, Gate 2 and
the staged memory are the node's, unchanged.

## What it keeps, and where

All of it lives in the per-user config directory:

- Linux: `~/.config/secdogie/app`;
- macOS: `~/Library/Application Support/secdogie/app`;
- Windows: `%APPDATA%\secdogie\app`;
- anywhere else: set `SECDOGIE_HOME`.

The directory is mode 0700, and each key file in it is 0600.

| File | What it is |
| --- | --- |
| `node.key` | The node's identity. |
| `app.key` | The window's session key. It signs messages and memory confirmations, never a Gate 2 approval. |
| `operator.keystore` | The operator key, sealed under your passphrase (Argon2id + XSalsa20-Poly1305). It is created at your first approval. |
| `journal.db`, `memory.db` | The node's signed journal and its memory quarantine. |

The API key goes where the agent reads it: next to the program when it is
packaged, otherwise in `~/.config/secdogie/config`.

## Security

- **The operator key.**
  - It exists on disk only in sealed form. The passphrase is never written
    anywhere.
  - Each approval unseals the key for one signature, only after the challenge
    has been checked. A challenge that cannot be signed never asks for the
    passphrase.
  - A wrong passphrase signs nothing. At the first approval, two passphrases
    that do not match set nothing.
  - Until the passphrase is set, the node trusts no operator key, so nothing
    high-risk can be approved at all.
  - There is no approve-all and no default verdict.
- **Capabilities.**
  - At each start, a fresh issuer key grants this node the desktop scopes and
    nothing else: look, click, type, press keys, scroll, drag, open. It does
    not grant `process.run` or anything on the network.
  - The issuer key is held in memory only and never written.
  - The grant lasts a day and is renewed while the window is open.
  - A grant never replaces Gate 2.
- **No screenshots are taken for the App.** The window shows the conversation,
  not your screen. It reads no process memory and talks to no server; the
  model provider is the agent's, as before.
- **One window per user.** A second window is refused rather than sharing the
  journal.

The mesh (joining other nodes, rendezvous, revocations) is the job of
`secdogie-node`, configured as before. The window's own node stays on loopback.

## Tests

```
pytest tests/ -q              # the Tk tests skip without a display
xvfb-run -a pytest tests/ -q  # as CI runs them
```

- `test_model.py`: the window's logic over the real controller, including the
  passphrase cases.
- `test_local.py`: the local node's files, permissions, trust and grant.
- `test_flow.py`: end to end, with a real node, the production runner and the
  real agent loop. Only the model and the desktop are stand-ins.
- `test_window.py`: the Tk view.
