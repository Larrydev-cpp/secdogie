# secdogie

Small, from-scratch pieces that combine into one idea: **let a
cloud vision-LLM control a computer you own, reached over a tunnel you
control.**

- [`tunnel/`](tunnel/) — a minimal point-to-point encrypted VPN tunnel,
  written from scratch in C on libsodium primitives (X25519 + BLAKE2b +
  XChaCha20-Poly1305). See [`tunnel/PROTOCOL.md`](tunnel/PROTOCOL.md) for
  the handshake design and [`tunnel/README.md`](tunnel/README.md) to build
  and run it.
- [`agent/`](agent/) — a vision-LLM computer-control agent: point it at a
  task in plain language, it screenshots your screen, asks a vision model
  what to do next, and executes one action at a time (click, type, scroll,
  ...) until the task is done. See [`agent/README.md`](agent/README.md).
- [`open/`](open/) — a GUI on top of `agent/` that splits the screen by open
  window and drives one `agent` instance per selected window at once,
  instead of one agent owning the whole screen. See
  [`open/README.md`](open/README.md).

## Downloads

Pre-built binaries (a single-file `secdogie-agent` executable for Linux,
Windows, and macOS, plus the `secdogie-tunnel` binary for Linux) are
published on the [Releases](../../releases) page. They're built and attached
automatically when a `v*` tag is pushed — see
[`docs/RELEASING.md`](docs/RELEASING.md). Prefer to build from source?
Each subdirectory's README has instructions.

## How they fit together

`agent/` only needs *some* screen and input device to drive — normally the
machine it's running on. If you want a cloud-hosted model to control a
*different* machine (e.g. your home desktop, reached from elsewhere), route
the agent's traffic to that machine through `secdogie-tunnel`: bring up the
tunnel between the two machines, then run the agent so its screenshots/
input calls target the remote box (e.g. over the tunnel's virtual network,
via VNC/RDP/X11-forwarding carried inside the tunnel, or by running the
agent directly on the remote machine and only using the tunnel to reach it
for setup/monitoring). The tunnel and the agent are independent, composable
pieces on purpose — neither hard-depends on the other.

## Before you run any of this

These pieces execute real, consequential actions: the tunnel moves real
network traffic, `agent` moves a real mouse and types on a real keyboard, and
`open` does that across several windows at once.

- **Only point the agent(s) at a computer you own or are explicitly
  authorized to control.** They are meant to automate your own machine, the
  same way you would use TeamViewer/VNC on yourself — not to be installed on
  someone else's computer without their knowledge or consent.
- Start with `agent`'s `--dry-run` flag (in `open`, leave **Enable real
  actions** off) and keep per-step confirmation on until you trust a given
  task; `open` runs unattended across every selected window once real
  actions are on, since a per-step prompt doesn't make sense across several
  windows sharing one terminal.
- None of these components has been independently security-audited. Read the
  "Known limitations" sections in each subproject's docs before relying on
  them for anything sensitive.

## Layout

```
tunnel/   C, libsodium-based VPN tunnel (PROTOCOL.md has the design + limitations)
agent/    Python vision-LLM computer-control agent (provider-agnostic action schema)
open/     Python GUI: split the screen by window, drive several agent instances at once
```

Each subdirectory has its own README with build/install/run instructions
and its own test suite.
