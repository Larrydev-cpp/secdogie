# secdogie atlas — Model Control Terminal

> **This branch is `windows`.** Same source as `platform/windows`, `platform/linux`, and `platform/macos`. See [PLATFORMS.md](PLATFORMS.md).


C++20. Three first-class operator OSes, **same source**. No TODOs, no empty functions.

| OS | UI tree | Memory inspect |
|---|---|---|
| **Windows** | `IUIAutomationTreeWalker` of the *target PID's* hwnds | `OpenProcess` `VM_READ\|QUERY` → `VirtualQueryEx` → `ReadProcessMemory` + SEH |
| **Linux** | compositor / AT-SPI not linked; memory is the live path | `/proc/<pid>/maps` → `process_vm_readv` |
| **macOS** | `AXUIElementCreateApplication(pid)` | `task_for_pid` → `mach_vm_region` → `mach_vm_read_overwrite` |

Long-lived branches (same tree, README lead-in differs):

- `platform/windows`
- `platform/linux`
- `platform/macos`

`atlas_inspect` never writes the target. TrustedInstaller / PPL / `VM_WRITE` / `ALL_ACCESS` / UAC bypass are refused.

## What this is

1. **UI tree first, memory on miss.** Windows UIA of the target pid (not the inspector's foreground window). macOS AX of that pid. Linux memory-primary.
2. **Read-only handle.** `PROCESS_VM_READ | QUERY` / `task_for_pid` / `process_vm_readv`. Write bits fail closed, not narrowed.
3. **Safe pages only.** `PAGE_READONLY` / `PAGE_READWRITE` (Windows), `r`/`rw` maps (Linux), `VM_PROT_READ`/`WRITE` (macOS). Guard / noaccess / execute skipped. 64 KiB chunks. Handle closed before return.
4. **Token wall.** `TOKEN_QUERY` only. SYSTEM / TI / PPL / higher integrity → `denied-escalate` or `denied-protected`.
5. **Pixel-diff after mutation.** Mutation is UIA Invoke / documented SendInput. Never `WriteProcessMemory`.

## What this is not

- **Not TrustedInstaller impersonation.** Always `RefusedIdentity`.
- **Not anti-EDR.** No unhook, no handle-hiding, no PPL bypass.
- **Not a UAC bypass.** SYSTEM launch is documented `CreateProcessAsUser` from an already-admin token.
- **Not lsass/csrss.** `ImageNameDenied` → `denied-protected`.
- **Not WriteProcessMemory / CreateRemoteThread / VirtualProtectEx.**

## Build

Windows (MSVC):

```bat
cmake -S . -B build
cmake --build build --config Release --target atlas_test atlas_inspect atlas_target
ctest --test-dir build -C Release --output-on-failure
.\build\Release\atlas_target.exe
.\build\Release\atlas_inspect.exe --pid <gui-pid> --json --find "Zoom Extents"
```

Linux:

```sh
cmake -S . -B build && cmake --build build --target atlas_test atlas_inspect atlas_target
ctest --test-dir build --output-on-failure
./build/atlas_target &
./build/atlas_inspect --pid $! --json --find "Zoom Extents"
```

macOS:

```sh
cmake -S . -B build && cmake --build build --target atlas_test atlas_inspect atlas_target
ctest --test-dir build --output-on-failure
./build/atlas_inspect --pid <gui-pid> --json --find "Zoom Extents"
```

On macOS, Accessibility must be granted to the inspector for AX trees. `task_for_pid` of an unrelated process needs a debugger entitlement; same-user descendants and self still work.

`atlas_target` on Windows is a real hwnd (`Zoom Extents` / `LAYER_DIMS` buttons). On Linux/macOS it plants the same heap strings / DIB / JSON so memory inspect has a live foreign PID.

`--json` dumps `platform`, token, VAD (`scanned` + `kind` + `protect_name`), strings + VA, DIB/PE/JSON, mapped modules, `uia.tree`, `windows[]`, hybrid, `--find`. Handle closed before JSON.

CI: `ubuntu-latest`, `windows-latest`, `macos-latest`.

## Files

| File | Role |
|---|---|
| `include/readonly_handle.h` + `src/readonly_handle.cpp` | RAII OpenProcess / task_for_pid / pid session; RPM / mach_vm_read / process_vm_readv |
| `include/unique_handle.h` + `src/unique_handle.cpp` | `HANDLE` RAII, `ScopedPrivilege` enable/disable |
| `include/token_wall.h` + `src/token_wall.cpp` | `TOKEN_QUERY` / uid; SID / integrity / TI / PPL |
| `include/memory_inspector.h` + `src/memory_inspector.cpp` | VAD walk, string/DIB/PE/JSON, mapped names |
| `include/hybrid_tree.h` + `src/hybrid_tree.cpp` | UI tree + memory fusion |
| `include/privilege_manager.h` + `src/privilege_manager.cpp` | Integrity, TI refusal, allowlisted `CreateProcessAsUser` |
| `include/process_perception.h` + `src/process_perception.cpp` | Toolhelp / `/proc` / `KERN_PROC`; UIA / AX / EnumWindows / CGWindowList |
| `include/hybrid_control_loop.h` + `src/hybrid_control_loop.cpp` | UIA Invoke / SendInput + GDI pixel-diff |
| `src/atlas_inspect.cpp` | MCT CLI (full JSON) |
| `src/atlas_target.cpp` | Win32 window + heap fixture (Linux/macOS: heap descendant) |
| `tests/test_memory_inspector.cpp` | Self + foreign-process inspect |
