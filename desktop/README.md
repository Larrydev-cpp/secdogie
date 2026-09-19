# secdogie-desktop

A **native desktop window** (tkinter) to control a [secdogie fleet](../fleet) --
one application, one window. Live nodes and tasks, with submit / stop / pause /
resume. Same control logic as the [web console](../console) (it reuses
`ConsoleController`), but a real window instead of a browser.

Because it's a native app it can hold the operator's DID key and **sign its own
commands** (`--operator-key`), which a browser can't do easily.

## Run

```sh
pip install -e identity -e fleet -e console -e desktop
secdogie-desktop --fleet-port 47810
# a window opens; nodes dial the coordinator (secdogie-fleet node --connect ...)
```

Secure (DID-authenticated coordinator + signed operator commands):

```sh
secdogie-identity genkey coordinator.key
secdogie-identity genkey operator.key   # put its DID in operators.conf
secdogie-desktop --identity coordinator.key --authorized authorized-nodes.conf \
                 --operator-key operator.key --operator-authorized operators.conf
```

On a headless host (no display) the command prints a clear message and points at
`secdogie-console` (the browser UI) instead.

## Layout

- `viewmodel.py` -- pure, tk-free presentation logic (rows, per-task actions,
  status line, command signing). Unit-tested headlessly.
- `app.py` -- `FleetWindow`, the tkinter view; imports tkinter lazily so the
  module (and the tests) load on a headless host.
- `cli.py` -- `secdogie-desktop`: runs the coordinator + opens the window.
