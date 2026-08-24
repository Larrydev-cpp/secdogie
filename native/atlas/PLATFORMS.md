# Platform branches

Same C++ tree. Check out the branch for the machine you operate:

| Branch | Operator OS | UI tree | Memory |
|---|---|---|---|
| [`platform/windows`](https://github.com/Larrydev-cpp/secdogie/tree/platform/windows) | Windows | `IUIAutomationTreeWalker` of the target PID | `OpenProcess` + `VirtualQueryEx` + `ReadProcessMemory` |
| [`platform/linux`](https://github.com/Larrydev-cpp/secdogie/tree/platform/linux) | Linux | compositor / AT-SPI not linked (memory is live) | `/proc/<pid>/maps` + `process_vm_readv` |
| [`platform/macos`](https://github.com/Larrydev-cpp/secdogie/tree/platform/macos) | macOS | `AXUIElementCreateApplication(pid)` | `task_for_pid` + `mach_vm_region` + `mach_vm_read_overwrite` |

CI runs all three (`ubuntu-latest`, `windows-latest`, `macos-latest`). Write bits, TrustedInstaller theft, PPL, UAC bypass stay refused on every branch.
