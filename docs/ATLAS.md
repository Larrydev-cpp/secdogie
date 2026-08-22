# Atlas: dual-tier perception, read-only handles, Cloudflare Tunnel

Atlas is the next-generation control path for secdogie on operator-owned
machines. Python decision core: [`agent/secdogie_agent/atlas.py`](../agent/secdogie_agent/atlas.py).
Windows-speed native twin: [`native/atlas/`](../native/atlas/).

## Dual-tier loop

1. **Primary targeting** is the OS accessibility tree (Windows UI Automation,
   AT-SPI, AX) — PID, hwnd, bounding box, AutomationId. Enable it with
   `--desktop-ax`. The live loop prefers `click_element` / native Invoke over
   guessing a pixel off a downscaled screenshot.
2. **Verification** is a pixel-diff of the control region before vs after the
   action (`screen.changed_ratio` in the agent loop; `atlas.changed_ratio` /
   C++ `PixelDiff` for the native path). No visible mutation → retry → fail.
   A no-mutation step is never recorded as success.
3. **`click_element` is in the retry-safe set**, so a UIA Invoke that didn't
   actually change the UI is retried and then reported honestly.

CAD canvases and custom-drawn chrome still fall back to vision. That's
intentional: the tree is empty there, and pixels remain the fallback, not the
default.

## Read-only process handles

`OpenProcess` requests are gated to `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
| PROCESS_QUERY_LIMITED_INFORMATION`. `PROCESS_VM_WRITE`, `PROCESS_VM_OPERATION`,
`PROCESS_CREATE_THREAD`, and `PROCESS_ALL_ACCESS` are **refused**, not silently
narrowed — a hallucinated write cannot slip through as "we dropped the dangerous
bits for you".

## Privilege wall

- **SYSTEM** is the documented `CreateProcessAsUser` path from an
  *already-admin* token, allowlisted at launch (`--allow-elevated-command`).
  It is not a UAC bypass. See [`elevate.py`](../agent/secdogie_agent/elevate.py).
- **`NT SERVICE\TrustedInstaller` impersonation is refused.** Token theft of
  the servicing identity is a security-boundary bypass. `elevate.try_impersonate_trusted_installer()`
  and `PrivilegeManager::TryImpersonateTrustedInstaller()` always return
  `refused-identity`.
- **Anti-EDR is refused.** No unhooking, handle-hiding, or foreign-process
  memory scans. Atlas uses OpenProcess (query/read), Toolhelp, EnumWindows,
  and UI Automation COM.

## Cloudflare Tunnel

For production reachability, prefer a **named Cloudflare Tunnel** over the
unaudited custom UDP `tunnel/`:

```sh
# Linux / macOS
native/atlas/scripts/setup-cloudflare-tunnel.sh secdogie-atlas atlas.example.com

# Windows
native/atlas/scripts/setup-cloudflare-tunnel.ps1 -Name secdogie-atlas -Hostname atlas.example.com
```

Config template: [`native/atlas/config/cf_tunnel_config.json`](../native/atlas/config/cf_tunnel_config.json)
and [`tunnel/cloudflare/`](../tunnel/cloudflare/). `cloudflared` makes
outbound-only connections; put Cloudflare Access in front of the hostname.
Do not commit credentials JSON.

The custom `tunnel/` remains for air-gapped lab use. See [`SECURITY.md`](../SECURITY.md).

## Build the native module

```sh
cmake -S native/atlas -B native/atlas/build
cmake --build native/atlas/build --target atlas_test
ctest --test-dir native/atlas/build --output-on-failure
```

Off Windows the library still builds; Win32 entry points return `Unsupported`
and the decision-core tests still pass.
