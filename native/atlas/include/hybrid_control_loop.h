#pragma once

// Dual-tier control loop. Mutation is per-OS and never HID on Darwin:
//
//   Windows : UIA Invoke / Toggle, then documented SendInput click.
//   macOS   : AX tree is a trackpad. Hit-test (x,y) → deepest AX node →
//             AXUIElementPerformAction(kAXPressAction). CGEventPost /
//             IOHID / Quartz HID click is refused. CGWindow capture is
//             pixel-diff VERIFY after the tap, not perception.
//   Linux   : no mutate. Memory inspect only (no AT-SPI, no HID).
//
// Verify: capture of the control (inflated) before and after; mean-absolute
// pixel-diff must exceed kDiffThreshold or the step retries, then fails.
// Capture is GDI (Windows) / CGWindow (macOS) / unsupported (Linux).
// A no-mutation "success" is never recorded.
//
// High-risk actions (save / delete / close) require an operator confirm
// flag. The vision model cannot set that flag.

#include "process_perception.h"
#include "privilege_error.h"

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace secdogie::atlas {

enum class ActionKind { Invoke, Toggle, Read, Confirm };
enum class StepStatus {
  Pending,
  Perceiving,
  Targeting,
  SnapshotBefore,
  Executing,
  SnapshotAfter,
  Verifying,
  Confirmed,
  Retrying,
  Fallback,
  Blocked,
  Failed,
  Passed
};

struct LoopAction {
  std::string id;
  std::wstring label;
  ActionKind kind = ActionKind::Invoke;
  Selector selector;
  bool high_risk = false;
  bool has_point = false;
  std::int32_t x = 0;
  std::int32_t y = 0;
};

struct Framebuffer {
  std::int32_t width = 0;
  std::int32_t height = 0;
  std::vector<std::uint8_t> bgra;  // width * height * 4
};

struct LoopStep {
  std::string action_id;
  StepStatus status = StepStatus::Pending;
  PerceptionMode mode = PerceptionMode::Uia;
  std::wstring target_name;
  Rect target_bounds;
  double diff_ratio = 0;
  std::string detail;
  std::uint32_t elapsed_ms = 0;
};

struct LoopConfig {
  double diff_threshold = 0.012;
  int max_retries = 2;
  bool vision_fallback = true;
  bool memory_fallback = true;
  bool operator_confirmed = false;
  int capture_pad_px = 8;
  std::uint32_t target_pid = 0;  // 0 = Snapshot() (foreground / self)
};

class PixelDiff {
 public:
  static double ChangedRatio(const Framebuffer& a, const Framebuffer& b);
  static std::string Hash(const Framebuffer& fb);
};

// "uia+sendinput" | "ax-press" | "none"
const char* MutationBackendName() noexcept;
// Windows SendInput fallback is HID. macOS AX and Linux are not.
bool MutationUsesHid() noexcept;

class HybridControlLoop {
 public:
  using CaptureFn = std::function<Result<Framebuffer>(const Rect&)>;
  using ExecuteFn = std::function<PrivilegeError(const ControlNode&, const LoopAction&)>;
  using SinkFn = std::function<void(const LoopStep&)>;

  HybridControlLoop(ProcessPerception perception, LoopConfig config);

  void SetCapture(CaptureFn fn) { capture_ = std::move(fn); }
  void SetExecute(ExecuteFn fn) { execute_ = std::move(fn); }

  LoopStep Run(const LoopAction& action, SinkFn sink = {});

  const PerceptionSnapshot& last_snapshot() const { return last_; }
  void SetLastSnapshot(PerceptionSnapshot s) { last_ = std::move(s); }
  LoopConfig& config() { return config_; }

  // Default capture: GDI BitBlt (Windows) / CGWindowListCreateImage (macOS).
  // Default execute: UIA+SendInput (Windows) / AXPress (macOS, never HID).
  static Result<Framebuffer> CaptureScreen(const Rect& r);
  // macOS: CGWindowListCreateImage of one window id — loop pixel-diff VERIFY
  // only. Not inspect graphics, not the Mac "image". The pad is the AX tree.
  // Not HID. Windows/Linux: unsupported — graphics there is heap DIB / none.
  static Result<Framebuffer> CaptureWindow(std::uint64_t hwnd);
  static PrivilegeError ExecuteDefault(const ControlNode& target,
                                       const LoopAction& action);

 private:
  ProcessPerception perception_;
  LoopConfig config_;
  PerceptionSnapshot last_;
  CaptureFn capture_;
  ExecuteFn execute_;
};

const char* StepStatusName(StepStatus s) noexcept;

}  // namespace secdogie::atlas
