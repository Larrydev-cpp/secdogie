# Atlas: dual-tier perception, read-only handles, Cloudflare Tunnel

中文：[ATLAS.zh.md](ATLAS.zh.md).

Atlas is the next-generation control path for secdogie on operator-owned
machines. Python decision core: [`agent/secdogie_agent/atlas.py`](../agent/secdogie_agent/atlas.py).
Windows-speed native twin: [`native/atlas/`](../native/atlas/).

## Memory fallback (UIA miss)

When the accessibility tree is empty (owner-drawn CAD chrome, UIA COM
failure), Atlas does **not** guess pixels as the next step. `InspectPid`:

1. `TOKEN_QUERY` of the operator and of the target (`QueryProcessToken`).
   SYSTEM / TrustedInstaller / PPL / higher-integrity targets are
   `denied-escalate` or `denied-protected`. The wall never duplicates a
   higher token to close the gap.
2. `OpenProcess` with `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION` only.
3. `VirtualQueryEx` / `/proc/<pid>/maps` — skip `PAGE_GUARD`, `PAGE_NOACCESS`,
   execute pages, file-backed mappings, lsass/csrss/PPL.
4. `ReadProcessMemory` / `process_vm_readv` in 64 KiB chunks (MSVC `__try`
   around the syscall). A failed page is skipped, never written.
5. UTF-16LE + UTF-8 string extract + `BITMAPINFOHEADER` + MZ/PE + JSON
   state blobs.
6. Fuse with any last-known UIA node by name (`HybridNode` source =
   `uia` / `memory` / `fused`).
7. **Close the handle and drop SeDebug before return.** There is no standing
   handle, no standing privilege, and no `WriteProcessMemory`.

The UIA tree itself is a real `ControlViewWalker` (GetFirstChild /
GetNextSibling), depth-capped, node-capped — not an empty `Walk()`.

CLI (Model Control Terminal):

```
native/atlas/atlas_inspect --list
native/atlas/atlas_inspect --pid <n>
native/atlas/atlas_inspect --self --token
```

## Dual-tier loop

1. **Primary targeting** is the OS accessibility tree (Windows UI Automation,
   AT-SPI, AX) — PID, hwnd, bounding box, AutomationId. Enable it with
   `--desktop-ax`. The live loop prefers `click_element` / native Invoke over
   guessing a pixel off a downscaled screenshot.
2. **Verification** is a pixel-diff of the control region before vs after the
   action (`screen.changed_ratio` in the agent loop; `atlas.changed_ratio` /
   C++ `PixelDiff` for the native path). No visible mutation → retry → fail.
   A no-mutation step is never recorded as success.
3. **Mutation is per-OS.** Windows: UIA Invoke, then documented `SendInput`.
   macOS: `AXPress` / `AXConfirm` only — `click_element` is **never** rewritten
   to `left_click` (pyautogui on Darwin is Quartz HID / `CGEventPost`). Linux:
   no native mutate. A no-mutation step is never recorded as success.
4. **`click_element` is in the retry-safe set.** A miss retries along the
   **same delivery path** (Invoke / AXPress again). On Windows only, a miss
   rewrites to `left_click`. The raw `click_element` kind is never forwarded
   to `backend.execute` — it is not a backend verb.
5. A UIA miss falls back to the **previous** snapshot (last-known tree), not
   the empty current frame. Name / AutomationId / role match is case-insensitive.
6. The live agent loop (`loop.py`) keeps last-known `element_targets` the same
   way: an empty accessibility frame does not wipe the listing, so a model that
   still holds `eN` refs from the previous step can resolve them. `click_element`
   retries go through `_deliver_action` (Invoke / AXPress; Windows may rewrite
   to `left_click`; macOS never does) — the raw kind is never forwarded to
   `backend.execute`.
7. `axtree.find_elements` name / AutomationId match is case-insensitive exact
   (not substring), aligned with Atlas `find_control`.

CAD canvases and custom-drawn chrome still fall back to vision. That's
intentional: the tree is empty there, and pixels remain the fallback, not the
default.

## Last-known tree

An empty current UIA tree **must not overwrite** last-known. C++
`HybridControlLoop::last_` updates only when this frame's `controls` is
non-empty; Python `keep_tree` / `coalesce_element_targets` and the web
`keepTree` follow the same contract.

Execute exceptions / non-ok privilege codes fail the step immediately — they
are not retried as "no mutation". Pixel-diff width/height/length mismatch is
100% changed (never a `min`-length compare that under-reports).

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
# Intel Mac: add -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 -DCMAKE_OSX_ARCHITECTURES=x86_64
cmake --build native/atlas/build --target atlas_test
ctest --test-dir native/atlas/build --output-on-failure
```

Off Windows the library still builds; Win32 entry points return `Unsupported`
and the decision-core tests still pass.
