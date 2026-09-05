# Platform branches

Same C++ tree. Check out the branch for the machine you operate:

| Branch | Operator OS | UI tree | Mutation | Memory | Decode |
|---|---|---|---|---|---|
| [`platform/windows`](https://github.com/Larrydev-cpp/secdogie/tree/platform/windows) | Windows | `IUIAutomationTreeWalker` of the target PID | UIA Invoke / Toggle, then documented `SendInput` (HID) | `OpenProcess` + `VirtualQueryEx` + `ReadProcessMemory` | UTF-16LE then UTF-8 |
| [`platform/linux`](https://github.com/Larrydev-cpp/secdogie/tree/platform/linux) | Linux | compositor / AT-SPI not linked (memory is live) | none (no HID, no AT-SPI click) | `/proc/<pid>/maps` + `process_vm_readv` | UTF-8 (CJK) then real UTF-16LE |
| [`platform/macos`](https://github.com/Larrydev-cpp/secdogie/tree/platform/macos) | macOS (Apple Silicon **and Intel x86_64**) | `AXUIElementCreateApplication(pid)` | `AXUIElementPerformAction(kAXPressAction)` only. `CGEventPost` / IOHID / Quartz HID click is refused. Capture is `CGWindowListCreateImage` (Screen Recording), not a HID event tap. | `task_for_pid` + `mach_vm_region` + `mach_vm_read_overwrite` | UTF-8 (CJK) then real UTF-16LE |

CI runs all three (`ubuntu-latest`, `windows-latest`, `macos-latest` arm64 + x86_64). Write bits, TrustedInstaller theft, PPL, UAC bypass stay refused on every branch.

Release assets: `atlas_mct` / `atlas_inspect` / `atlas_target` for windows-x86_64 (`.exe`), linux-x86_64, macos-arm64, macos-x86_64 (Intel Macs, `CMAKE_OSX_DEPLOYMENT_TARGET=11.0`), and macos-universal (`lipo` of arm64+x86_64). `atlas_mct` is the operator command plane and binds **127.0.0.1 only**. Intel Mac is cross-compiled from `macos-latest` (`CMAKE_OSX_ARCHITECTURES=x86_64`). Retired `macos-13` runners are not used; a failed arch no longer blocks publishing the rest.
