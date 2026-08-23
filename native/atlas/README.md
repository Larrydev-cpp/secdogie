# secdogie atlas — Model Control Terminal

C++20. No TODOs, no empty functions. This is the native product:
`atlas_inspect` is a lightweight terminal that reads a local GUI process,
falls back from UIA into deep memory, and never writes the target.

## What this is

1. **UIA / process / window tree** is the primary targeting path (PID, hwnd, bounding box, automationId). The tree is a real `IUIAutomationTreeWalker` walk (GetFirstChild / GetNextSibling), not a flat FindAll and not an empty `Walk()`.
2. **When UIA is empty**, `InspectPid` opens a *read-only* process handle, walks committed `PAGE_READONLY` / `PAGE_READWRITE` regions (`VirtualQueryEx` / `/proc/<pid>/maps`), copies them with `ReadProcessMemory` / `process_vm_readv` in 64 KiB chunks under SEH (MSVC) / errno, extracts UTF-16LE + UTF-8 strings, BITMAPINFOHEADER, MZ/PE headers, and JSON state blobs, fuses them with any last-known UIA nodes (`HybridNode`), then **closes the handle before return**.
3. **Token wall.** `TOKEN_QUERY` only. SYSTEM / TrustedInstaller / PPL / higher-integrity targets return `denied-escalate` or `denied-protected`. SeDebug is opt-in `ScopedPrivilege` and is **disabled in the destructor**. No standing handle, no standing privilege.
4. **Pixel-diff verification** after every mutating action. Mutation is still UIA Invoke / SendInput — never `WriteProcessMemory`.

## What this is not

- **Not TrustedInstaller impersonation.** Always `RefusedIdentity`.
- **Not anti-EDR.** No unhook, no handle-hiding, no PPL bypass.
- **Not a UAC bypass.** SYSTEM launch is still documented `CreateProcessAsUser` from an already-admin token. Privileges enabled for that call are dropped when `ScopedPrivilege` destructs.
- **Not lsass/csrss.** `ImageNameDenied` returns `denied-protected`.
- **Not WriteProcessMemory / CreateRemoteThread / VirtualProtectEx.** Perception only.
- **Not a web UI.** The operator surface is this CLI.

## Build

```sh
cmake -S . -B build
cmake --build build --target atlas_test atlas_inspect
ctest --test-dir build --output-on-failure
./build/atlas_inspect --list
./build/atlas_inspect --pid <gui-pid>
./build/atlas_inspect --self --token
./build/atlas_inspect --self --json
```

Off Windows the inspector is live: it uses `process_vm_readv` against a real PID. The tests fork a child, plant a heap marker, and assert the parent can read it. `HybridControlLoop` on Linux reports this process so the memory fallback is the primary path, not a stub.

## Files

| File | Role |
|---|---|
| `include/readonly_handle.h` + `src/readonly_handle.cpp` | RAII OpenProcess / pid session, `Read()` with SEH |
| `include/unique_handle.h` + `src/unique_handle.cpp` | HANDLE RAII, `ScopedPrivilege` enable/disable |
| `include/token_wall.h` + `src/token_wall.cpp` | TOKEN_QUERY, identity, inspect allow/deny |
| `include/memory_inspector.h` + `src/memory_inspector.cpp` | VAD walk, RPM, string/DIB/PE/JSON extract, safe boundary |
| `include/hybrid_tree.h` + `src/hybrid_tree.cpp` | UIA + memory fusion |
| `include/privilege_manager.h` + `src/privilege_manager.cpp` | Integrity, TI refusal, allowlisted SYSTEM |
| `include/process_perception.h` + `src/process_perception.cpp` | Toolhelp, EnumWindows, UIA tree walker, /proc list |
| `include/hybrid_control_loop.h` + `src/hybrid_control_loop.cpp` | UIA → last-known → memory inspect → pixel-diff |
| `src/atlas_inspect.cpp` | Model Control Terminal CLI |
| `tests/test_memory_inspector.cpp` | Real child-process inspect + live hybrid loop |
