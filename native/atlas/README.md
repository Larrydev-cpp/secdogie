# secdogie atlas — Model Control Terminal

> **This branch is `macos`.** Same source as `platform/windows`, `platform/linux`, and `platform/macos`. See [PLATFORMS.md](PLATFORMS.md).


C++20. Three first-class operator OSes, **same source**. No TODOs, no empty functions.

| OS | UI tree | Memory inspect | Decode |
|---|---|---|---|
| **Windows** | `IUIAutomationTreeWalker` of the *target PID's* hwnds | `OpenProcess` `VM_READ\|QUERY` → `VirtualQueryEx` → `ReadProcessMemory` + SEH | UTF-16LE primary, UTF-8 secondary. JSON is UTF-8. |
| **Linux** | compositor / AT-SPI not linked; memory is the live path | `/proc/<pid>/maps` → `process_vm_readv` | UTF-8 primary (CJK kept). UTF-16LE only if it is real wide text, not ASCII-pair garbage. |
| **macOS** | `AXUIElementCreateApplication(pid)` | `task_for_pid` → `mach_vm_region` (`shared`, not `share_mode`) → `mach_vm_read_overwrite` | Same as Linux. AX titles via `kCFStringEncodingUTF8`. |

Long-lived branches (same tree, README lead-in differs):

- `platform/windows`
- `platform/linux`
- `platform/macos`

`atlas_inspect` never writes the target. TrustedInstaller / PPL / `VM_WRITE` / `ALL_ACCESS` / UAC bypass are refused.

The **operator command plane** is `atlas_mct` — Windows `atlas_mct.exe`, Linux/macOS `atlas_mct`. It binds **127.0.0.1 only** (loopback). `0.0.0.0` / LAN / wildcard are refused. Port is operator-chosen (`--listen 127.0.0.1:PORT`, `0` = ephemeral, default 17890).

```bat
atlas_mct.exe --listen 127.0.0.1:17890
```

```sh
./atlas_mct --listen 127.0.0.1:17890
# POST /cmd  {"line":"inspect atlas_target"}
# GET  /health  GET /list
```

Commands: `list` · `inspect <pid|name>` · `find <control>` · `chain` / `串联` · `link <pid>` · `job report` / `报表` · `graphics`. Same read-only inspect as `atlas_inspect`.

A **job** is a chain of related processes (parent / child / same family / operator `link` — CAD drawing + report workbook, acad + accoreconsole). One unread PID, a dead related process, or a jittered inspect is **isolated**: last-known snapshot is kept, the vanished PID stays on the chain, the rest of the job continues. The whole job only fails if every stage fails with no last-known snapshot.

## What this is

1. **UI tree first, memory on miss.** Windows UIA of the target pid. macOS AX of the **frontmost / target pid** (title, description, value, bounds); CGWindow list if Accessibility is not granted. SIP may block `task_for_pid` — the UI tree still reads. Linux memory-primary.
2. **Read-only handle.** `PROCESS_VM_READ | QUERY` / `task_for_pid` / `process_vm_readv`. Write bits fail closed, not narrowed.
3. **Safe pages only.** `PAGE_READONLY` / `PAGE_READWRITE` (Windows), `r`/`rw` maps (Linux), `VM_PROT_READ`/`WRITE` (macOS). Guard / noaccess / execute skipped. 64 KiB chunks. Handle closed before return.
4. **Token wall.** `TOKEN_QUERY` only. SYSTEM / TI / PPL / higher integrity → `denied-escalate` or `denied-protected`.
5. **Pixel-diff after mutation.** Mutation is per-OS and **never HID on Darwin**:
   Windows = UIA Invoke / Toggle, then documented `SendInput`. macOS = `AXUIElementPerformAction(kAXPressAction)` only (`CGEventPost` / IOHID / Quartz click refused). Linux = no mutate (read-only inspect). Never `WriteProcessMemory`.

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
cmake --build build --config Release --target atlas_test atlas_inspect atlas_mct atlas_target
ctest --test-dir build -C Release --output-on-failure
.\build\Release\atlas_target.exe
.\build\Release\atlas_mct.exe --listen 127.0.0.1:17890
.\build\Release\atlas_inspect.exe --pid <gui-pid> --json --find "Zoom Extents"
```

Linux:

```sh
cmake -S . -B build && cmake --build build --target atlas_test atlas_inspect atlas_mct atlas_target
ctest --test-dir build --output-on-failure
./build/atlas_target &
./build/atlas_mct --listen 127.0.0.1:17890 &
./build/atlas_inspect --pid $! --json --find "Zoom Extents"
```

macOS (Apple Silicon native, or Intel x86_64 for Core / i-series Macs):

```sh
# Apple Silicon (arm64)
cmake -S . -B build -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0
cmake --build build --target atlas_test atlas_inspect atlas_mct atlas_target
ctest --test-dir build --output-on-failure

# Intel Mac (x86_64) — Big Sur through Sequoia. Cross from Apple Silicon:
cmake -S . -B build-intel -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 -DCMAKE_OSX_ARCHITECTURES=x86_64
cmake --build build-intel --target atlas_test atlas_inspect atlas_mct atlas_target
lipo -info build-intel/atlas_mct   # must contain x86_64
# run the binary under Rosetta; do not wrap host `ctest` with `arch -x86_64`
arch -x86_64 ./build-intel/atlas_test

# Universal (arm64 + x86_64) for operators who don't know the chip:
lipo -create build/atlas_mct build-intel/atlas_mct -output atlas_mct
./atlas_mct --listen 127.0.0.1:17890
./build/atlas_inspect --pid <gui-pid> --json --find "Zoom Extents"
```

On macOS, Accessibility must be granted to the inspector for AX trees. `task_for_pid` of an unrelated process needs a debugger entitlement; same-user descendants and self still work.

`atlas_target` on Windows is a real hwnd (`Zoom Extents` / `LAYER_DIMS` buttons). On Linux/macOS it plants the same heap strings / DIB / JSON so memory inspect has a live foreign PID.

`--json` dumps `platform`, `decode` (primary/secondary encoding), token, VAD (`scanned` + `kind` + `protect_name`), strings + VA + encoding, DIB/PE/JSON, mapped modules, `uia.tree`, `windows[]`, hybrid, `--find`. Handle closed before JSON. Wide strings are emitted as UTF-8; non-ASCII is never replaced with `?`.

CI: `ubuntu-latest`, `windows-latest`, `macos-latest` (arm64 native + x86_64 via `CMAKE_OSX_ARCHITECTURES`, `CMAKE_OSX_DEPLOYMENT_TARGET=11.0`). Release ships `atlas-macos-arm64`, `atlas-macos-x86_64` (Intel Macs), and `atlas-macos-universal` (`lipo`). Retired `macos-13` runners are not used.

## Files

| File | Role |
|---|---|
| `include/readonly_handle.h` + `src/readonly_handle.cpp` | RAII OpenProcess / task_for_pid / pid session; RPM / mach_vm_read / process_vm_readv |
| `include/unique_handle.h` + `src/unique_handle.cpp` | `HANDLE` RAII, `ScopedPrivilege` enable/disable |
| `include/token_wall.h` + `src/token_wall.cpp` | `TOKEN_QUERY` / uid; SID / integrity / TI / PPL |
| `include/memory_inspector.h` + `src/memory_inspector.cpp` | VAD walk, per-OS string decode (UTF-16LE / UTF-8 + CJK), DIB/PE/JSON |
| `include/utf.h` | WideToUtf8 / Utf8ToWide — JSON and UI ids are UTF-8 |
| `include/hybrid_tree.h` + `src/hybrid_tree.cpp` | UI tree + memory fusion |
| `include/privilege_manager.h` + `src/privilege_manager.cpp` | Integrity, TI refusal, allowlisted `CreateProcessAsUser` |
| `include/process_perception.h` + `src/process_perception.cpp` | Toolhelp / `/proc` / `KERN_PROC`; UIA / AX / EnumWindows / CGWindowList |
| `include/hybrid_control_loop.h` + `src/hybrid_control_loop.cpp` | Windows UIA+SendInput / macOS AXPress (never HID) / Linux none + GDI/CGWindow pixel-diff |
| `src/inspect_json.cpp` | UTF-8 JSON dump shared by CLI and MCT |
| `src/mct_command.cpp` | Command interpreter (`list` / `inspect` / `find` / `chain` / `job report`) |
| `include/process_chain.h` + `src/process_chain.cpp` | parent/child/family/link graph; isolated job runner; last-known members kept on death/jitter |
| `src/mct_server.cpp` | Loopback-only HTTP (`127.0.0.1`, never `0.0.0.0`) |
| `src/atlas_inspect.cpp` | MCT CLI (full JSON) |
| `src/atlas_mct.cpp` | Long-lived terminal EXE / app (`--listen 127.0.0.1:PORT`) |
| `src/atlas_target.cpp` | Win32 window + heap fixture (Linux/macOS: heap descendant) |
| `tests/test_memory_inspector.cpp` | Self + foreign-process inspect |
