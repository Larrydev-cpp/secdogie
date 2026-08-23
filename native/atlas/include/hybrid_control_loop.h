#pragma once

// Dual-tier control loop.
//
//   1. Perception: UIA / window tree picks the exact control (PID, hwnd,
//      bounding box, automationId).
//   2. Action: Invoke / Toggle via UIA patterns, or a coordinate click if
//      UIA failed and vision fallback is armed.
//   3. Verify: GDI capture of the control (inflated) before and after;
//      mean-absolute pixel-diff must exceed kDiffThreshold or the step
//      retries, then fails. A no-mutation "success" is never recorded.
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
};

class PixelDiff {
 public:
  static double ChangedRatio(const Framebuffer& a, const Framebuffer& b);
  static std::string Hash(const Framebuffer& fb);
};

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

  // Default capture: BitBlt of a screen rect. Default execute: UIA Invoke
  // with a SendInput click fallback.
  static Result<Framebuffer> CaptureScreen(const Rect& r);
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
