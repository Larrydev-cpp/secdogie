# secdogie atlas — native hybrid loop

Drop this folder into `secdogie/native/atlas/` (or build in place).

## What this is

The next-generation **dual-tier perception & control loop** for secdogie:

1. **UIA / process / window tree** is the primary targeting path (PID, hwnd, bounding box, automationId).
2. **Pixel-diff verification** after every mutating action. No visible change → retry → fail. A no-mutation success is never recorded.
3. **Read-only process handles.** `PROCESS_VM_WRITE`, `PROCESS_VM_OPERATION`, `PROCESS_CREATE_THREAD`, and `PROCESS_ALL_ACCESS` are refused, not silently narrowed.
4. **Cloudflare named tunnel** as the production reachability layer. The custom `tunnel/` stays for air-gapped labs.

## What this is not

- **Not TrustedInstaller impersonation.** `PrivilegeManager::TryImpersonateTrustedInstaller()` always returns `RefusedIdentity`. Token theft of `NT SERVICE\TrustedInstaller` is a Windows servicing-boundary bypass. secdogie `SECURITY.md` already refuses UAC bypasses; this is the same wall, one identity higher.
- **Not anti-EDR.** `TryEdrEvasion()` is a documented refusal. Atlas uses OpenProcess (query/read), Toolhelp, EnumWindows, and UI Automation COM — the documented APIs. It does not unhook, hide handles, or scan foreign process memory.
- **Not a UAC bypass.** SYSTEM launch is the documented `CreateProcessAsUser` path from an *already-admin* token, allowlisted at process start, same contract as `agent/secdogie_agent/elevate.py`.

## Build

```sh
cmake -S . -B build
cmake --build build --target atlas_test
ctest --test-dir build --output-on-failure
```

Windows needs the Windows SDK (ole32, uiautomation, user32, gdi32). Off Windows the library still builds; Win32 entry points return `Unsupported` and the decision-core tests still pass.

## Files

| File | Role |
|---|---|
| `include/privilege_error.h` | Result / error codes |
| `include/readonly_handle.h` + `src/readonly_handle.cpp` | RAII read-only `OpenProcess` |
| `include/privilege_manager.h` + `src/privilege_manager.cpp` | Integrity, TI refusal, allowlisted SYSTEM |
| `include/process_perception.h` + `src/process_perception.cpp` | Toolhelp, EnumWindows, UIA tree |
| `include/hybrid_control_loop.h` + `src/hybrid_control_loop.cpp` | UIA target + GDI pixel-diff loop |
| `tests/test_atlas.cpp` | Self-contained unit tests |
| `config/cf_tunnel_config.json` | Named-tunnel spec |
| `scripts/setup-cloudflare-tunnel.ps1` / `.sh` | Operator setup |

## Guardrails

Handle access denial, UIA COM failure, and missing desktop session all degrade along documented paths: `AccessDenied` / `VisionFallback` / `NoSession`. There is no "try harder with a stolen token" branch.
