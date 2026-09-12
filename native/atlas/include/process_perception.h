#pragma once

// Process + window + UI tree perception.
//
// Three first-class operator OSes, same source:
//   Windows : IUIAutomationTreeWalker of the *target pid's* hwnds
//   Linux   : process list via /proc; window tree is compositor-dependent
//             (memory inspect is the live path: process_vm_readv)
//   macOS   : BOTH the AX tree AND a trackpad hit. AX names/roles/bounds are
//             the fine pad; AXUIElementCopyElementAtPosition is the OS finger;
//             AXPress is the tap. Accessibility is a TCC grant, not a product
//             refusal — when it is off, CGWindow *bounds* (no Screen Recording
//             required) are the coarse pad. Screen Recording only fills titles
//             and loop pixel-diff VERIFY. Never HID / CGEvent / IOHID.
//             task_for_pid is memory-only and is not required to read the pad.
//
// PROCESS_VM_READ / mach_vm_read / process_vm_readv is used to name modules
// and to dump *safe* committed pages in memory_inspector.cpp — never to write.

#include "privilege_error.h"

#include <cstdint>
#include <string>
#include <vector>

namespace secdogie::atlas {

enum class PerceptionMode { Uia, VisionFallback, Memory };

enum class ControlRole {
  Window,
  Pane,
  Button,
  TabItem,
  TreeItem,
  Edit,
  Text,
  MenuItem,
  ToolBar,
  Custom
};

struct Rect {
  std::int32_t x = 0;
  std::int32_t y = 0;
  std::int32_t w = 0;
  std::int32_t h = 0;
};

inline bool RectValid(const Rect& r) noexcept { return r.w > 0 && r.h > 0; }

inline bool RectContains(const Rect& r, std::int32_t x, std::int32_t y) noexcept {
  return RectValid(r) && x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h;
}

struct ProcessInfo {
  std::uint32_t pid = 0;
  std::wstring image;
  std::uint32_t session_id = 0;
};

struct WindowInfo {
  std::uint64_t hwnd = 0;
  std::uint32_t pid = 0;
  std::wstring title;
  std::wstring class_name;
  Rect bounds;
  bool visible = false;
};

struct ControlNode {
  std::string id;
  ControlRole role = ControlRole::Custom;
  std::wstring name;
  std::wstring automation_id;
  Rect bounds;
  std::uint32_t pid = 0;
  std::uint64_t hwnd = 0;
  bool enabled = true;
  std::vector<ControlNode> children;
};

struct Selector {
  std::wstring automation_id;
  std::wstring name;
  ControlRole role = ControlRole::Custom;
  bool has_role = false;
};

struct PerceptionSnapshot {
  PerceptionMode mode = PerceptionMode::Uia;
  ProcessInfo process;
  WindowInfo window;
  std::vector<ControlNode> controls;
  std::string detail;
};

struct ListedProcess {
  std::uint32_t pid = 0;
  std::uint32_t ppid = 0;
  std::wstring image;
  std::wstring cmdline;
  std::uint32_t session_id = 0;
  std::uint64_t rss_kb = 0;
  bool readable = true;
};

// TCC / OS grants for the pad. Never HID. Never elevate.
// pad: "ax" | "cgwindow" | "uia" | "memory"
struct PadGrants {
  bool accessibility = false;
  bool screen_recording = false;
  std::string pad;
  std::string detail;
};

PadGrants QueryPadGrants() noexcept;
// macOS: AXIsProcessTrustedWithOptions(prompt) + CGRequestScreenCaptureAccess.
// Opens System Settings if still untrusted. No-op on Windows/Linux. Never HID.
void RequestPadGrants();
// Window-level pad (CGWindow / EnumWindows) for a pid — works without AX.
std::vector<ControlNode> WindowPad(std::uint32_t pid);

class ProcessPerception {
 public:
  static constexpr int kMaxTreeDepth = 40;
  static constexpr std::size_t kMaxTreeNodes = 4000;

  PerceptionSnapshot Snapshot();
  PerceptionSnapshot SnapshotPid(std::uint32_t pid);

  static std::vector<WindowInfo> ListWindows();

  static std::vector<ListedProcess> ListProcesses();

  static const ControlNode* Find(const std::vector<ControlNode>& roots,
                                 const Selector& selector);
  static void Flatten(const std::vector<ControlNode>& roots,
                      std::vector<const ControlNode*>& out);
  // Deepest / smallest box containing (x, y). The Mac trackpad analogue:
  // a window also contains the point; the button under the finger is the hit.
  static const ControlNode* HitTest(const std::vector<ControlNode>& roots,
                                    std::int32_t x, std::int32_t y);

 private:
  PerceptionSnapshot SnapshotWindows();
  PerceptionSnapshot SnapshotLinux();
  PerceptionSnapshot SnapshotDarwin();
};

const char* RoleName(ControlRole r) noexcept;
ControlRole RoleFromUiaType(int control_type) noexcept;

}  // namespace secdogie::atlas
