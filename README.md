# secdogie

A companion that runs only on your own devices.

- **It asks before it acts.** Every goal passes two Socratic gates. High-risk
  steps always wait for your signature: no mode or switch turns that off.
- **Your devices are one mesh.** Pair a phone, a tablet or another computer
  over WebRTC: compare six digits, sign an admission. There is no central
  server and no public node.
- **It reads structure, not pixels.** It sees the accessibility tree; the
  desktop app never captures the screen.

The plan, the decisions and what comes next are in [`PLAN.md`](PLAN.md).

## Download

Each [release](https://github.com/Larrydev-cpp/secdogie/releases) carries:

| File | What it is |
| --- | --- |
| `secdogie-studio-windows-x86_64-<version>-setup.exe` | Studio for Windows. It installs for you only (no administrator) and starts nothing at login. |
| `secdogie-studio-macos-<arch>-<version>.dmg` | Studio for macOS, `arm64` or `x86_64`. |
| `secdogie-studio-linux-x86_64-<version>.AppImage` | Studio for Linux: `chmod +x`, then run it. |
| `secdogie-studio-<platform>-<version>-portable.zip` | The same app, without installing. |
| `secdogie-agent-…`, `secdogie-atlas-…`, `secdogie-tunnel-…` | The agent as one executable, the read-only structure tools, and the encrypted tunnel. |

Builds are not code-signed yet:
- **macOS:** right-click the app and choose **Open**.
- **Windows:** choose **More info → Run anyway**.

Run any Studio build with `--self-test` to check it. It walks a throwaway
profile, a node, a link and a WebRTC loopback.

## Studio

The desktop app is a wallet. This node, what waits on you, and each of your
devices are cards; pick one to bring it forward and read what it holds.

- **Talk:** tell it what you want. It asks when unsure and asks you to sign
  anything irreversible.
- **Devices:** write an invitation (a QR code or a link). The other device
  opens it and agrees; you admit it when both screens show the same six
  digits. Go online to link every device directly.
- **Rules:** tighten the gates with signed YAML rules. Rules can only make
  the gates stricter.
- **Ink and paper:** a night theme and a day theme, with a switch that floods
  the window from one to the other. Chinese and English throughout.

Closing the window leaves a small light in the corner of the screen. Quitting
stops the node. Nothing runs at login.

## What is inside

| Part | What it does |
| --- | --- |
| `identity/` | DIDs (Ed25519 `did:key`), canonical signing, allowlists |
| `transport/` | Signed sessions over UDP or WebRTC: rendezvous, relays, membership, pairing, mesh links |
| `citadel/` | The signed journal and state, replication, both Socratic gates, staged memory, the decision model |
| `node/` | `secdogie-node`: the resident node that assembles all of the above |
| `studio/` | `secdogie-studio`: the desktop app, which runs the node as its child |
| `policy/` | Signed, declarative rule bundles, which can only tighten |
| `webrtc/` | Pairing and "My devices" in the browser, plus the signaling gateway |
| `dialogue/`, `inference/`, `agent/`, `desktop/`, `native/atlas/` | The operator dialogue, the inference mesh, structured perception, and native read-only tools |
| `tunnel/`, `fleet/`, `console/` | The encrypted C tunnel and operations tools |

## Safety boundary

These never enter the code:
- process memory writes or remote thread injection;
- kernel HID;
- EDR evasion or anti-detection;
- covert persistence or privilege escalation;
- bypassing user authorization or the OS permissions for accessibility and
  screen recording;
- turning human approval into default auto-approval;
- covertly embedding third-party services;
- traffic obfuscation, or hole punching used to evade detection.

These always hold:
- memory is read-only;
- execution is supervised;
- high-risk steps fail closed and are confirmed by a person at every entry
  point;
- physical action needs an explicit capability.

Saved web sessions are reused only for read-only browsing of sites you logged
into yourself. Credentials are never typed, and logins, CAPTCHAs and
anti-bot measures are never bypassed. Every change is checked against this
list before it is committed.

## Security

Report vulnerabilities privately through a
[GitHub security advisory](https://github.com/Larrydev-cpp/secdogie/security/advisories/new).
Include the component, the commit and the steps to reproduce.

`tunnel/` has not had an independent cryptographic audit. Where a real
adversary is on the wire, use WireGuard or a named Cloudflare Tunnel.

## Build and test

```sh
pip install -e ./identity -e "./transport[webrtc]" -e ./citadel -e ./policy -e ./dialogue -e ./node -e "./studio[gui]"
secdogie-studio                        # the desktop app
python -m pytest transport/tests citadel/tests studio/tests -q
ruff check .
cd webrtc && npm test && npm run e2e   # browser pairing and mesh (needs Playwright and Chromium)
```

CI runs every package's tests, the C tunnel's tests, the lint, and the
browser end-to-end tests over real WebRTC.

## Status

- **Done:**
  - identity, the P2P mesh, the signed journal and its replication;
  - both gates and staged memory in the live loop;
  - the node;
  - Studio with pairing;
  - browser pairing and the mesh.
- **Under way:**
  - spreading policy bundles across the mesh;
  - revoking devices;
  - the node's dialogue over WebRTC.
- **Honest gap:** on Windows and Linux the agent's live loop still takes
  screenshots. Moving it to structure first is in the plan.

## License

See [`LICENSE`](LICENSE). Studio bundles Google Sans under the SIL Open Font
License 1.1 (`studio/secdogie_studio/qml/fonts/OFL.txt`).
