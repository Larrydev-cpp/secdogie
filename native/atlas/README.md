# secdogie atlas — Model Control Terminal

**Target platform: Windows.** C++20. No TODOs, no empty functions.

`atlas_inspect` is a lightweight terminal that reads a local GUI process on
the operator's Windows machine: UI Automation first, then a *read-only*
`OpenProcess` + `VirtualQueryEx` + `ReadProcessMemory` walk, never a write.

Linux (`process_vm_readv`, `/proc/<pid>/maps`) is a **port** of the same
source so the decision core can be tested off a Windows box. It is not the
product surface.

## What this is

1. **UIA / window tree is primary.** PID, hwnd, bounding box, `AutomationId`.
   Real `IUIAutomationTreeWalker` (`GetFirstChild` / `GetNextSibling`) of the
   *target pid's* visible windows — not the foreground window of the inspector,
   not a flat `FindAll`, not an empty `Walk()`.
2. **When UIA is empty** (owner-drawn CAD, no hwnd), `InspectPid` opens a
   read-only handle (`PROCESS_VM_READ | PROCESS_QUERY_INFORMATION` only),
   walks committed `PAGE_READONLY` / `PAGE_READWRITE` with `VirtualQueryEx`,
   copies 64 KiB chunks under MSVC `__try/__except` so a bad page cannot
   crash *this* process, extracts UTF-16LE + UTF-8, `BITMAPINFOHEADER`,
   MZ/PE, JSON, fuses with last-known UIA nodes, **closes the handle
   before return**.
3. **Token wall.** `TOKEN_QUERY` only. SYSTEM / TrustedInstaller / PPL /
   higher-integrity targets return `denied-escalate` or `denied-protected`.
   SeDebug is opt-in `ScopedPrivilege` and is **disabled in the destructor**.
4. **Pixel-diff after mutation.** Mutation is UIA `Invoke` / `Toggle` or
   documented `SendInput`. Never `WriteProcessMemory`.
5. **SYSTEM launch** is documented `CreateProcessAsUser` from an already-admin
   token into the interactive session. Not a UAC bypass.

## What this is not

- **Not TrustedInstaller impersonation.** Always `RefusedIdentity`.
- **Not anti-EDR.** No unhook, no handle-hiding, no PPL bypass.
- **Not a UAC bypass.**
- **Not lsass/csrss.** `ImageNameDenied` → `denied-protected`.
- **Not WriteProcessMemory / CreateRemoteThread / VirtualProtectEx.**
- **Not a web UI.** The operator surface is this CLI (and the optional
  console that shells it).

## Build (Windows / MSVC)

```bat
cmake -S . -B build
cmake --build build --config Release --target atlas_test atlas_inspect atlas_target
ctest --test-dir build -C Release --output-on-failure
.\build\Release\atlas_inspect.exe --list --json
.\build\Release\atlas_inspect.exe --pid <gui-pid> --json --find "Zoom Extents"
.\build\Release\atlas_target.exe
```

`atlas_target` on Windows is a real hwnd: buttons named `Zoom Extents` /
`LAYER_DIMS` so UIA can see them, plus the same planted heap strings / DIB /
JSON for the memory fallback.

`--json` dumps the full snapshot: `platform`, token, VAD (`scanned` + `kind`
heap/image/file/anon + `protect_name` `PAGE_READWRITE` / `PAGE_READONLY`),
strings with remote VA, DIB / PE / JSON, mapped modules (from
`GetMappedFileNameW` / `EnumProcessModules`), **the UIA tree of that pid**
(`uia.tree` with hwnd / AutomationId / bounds, not just a node count),
visible hwnds, hybrid nodes, `--find`. Handle closed before JSON.

GitHub Actions runs `atlas-tests` on `ubuntu-latest` (Linux port) **and**
`atlas-tests-windows` on `windows-latest` (MSVC).

## Linux port

Same tree. `VirtualQueryEx` → `/proc/<pid>/maps`. `ReadProcessMemory` →
`process_vm_readv` (no SEH; errno). Yama `ptrace_scope=1` hides unrelated
PIDs; inspect a descendant (`atlas_target`) in that case.

```sh
cmake -S . -B build && cmake --build build --target atlas_test atlas_inspect
./build/atlas_inspect --pid <n> --json --find "Zoom Extents"
```

## Files

| File | Role |
|---|---|
| `include/readonly_handle.h` + `src/readonly_handle.cpp` | RAII `OpenProcess`, `ReadProcessMemory` + SEH, command line, working set |
| `include/unique_handle.h` + `src/unique_handle.cpp` | `HANDLE` RAII, `ScopedPrivilege` enable/disable |
| `include/token_wall.h` + `src/token_wall.cpp` | `TOKEN_QUERY`, SID / integrity / TI / PPL |
| `include/memory_inspector.h` + `src/memory_inspector.cpp` | `VirtualQueryEx`, RPM, string/DIB/PE/JSON, `GetMappedFileNameW` |
| `include/hybrid_tree.h` + `src/hybrid_tree.cpp` | UIA + memory fusion |
| `include/privilege_manager.h` + `src/privilege_manager.cpp` | Integrity, TI refusal, allowlisted `CreateProcessAsUser` |
| `include/process_perception.h` + `src/process_perception.cpp` | Toolhelp, `EnumWindows`, per-pid UIA walker, cmdline via `NtQueryInformationProcess` |
| `include/hybrid_control_loop.h` + `src/hybrid_control_loop.cpp` | UIA Invoke / SendInput + GDI pixel-diff |
| `src/atlas_inspect.cpp` | MCT CLI (full JSON) |
| `src/atlas_target.cpp` | Win32 window + heap fixture (Linux: heap-only descendant) |
| `tests/test_memory_inspector.cpp` | Self + foreign-process inspect |
