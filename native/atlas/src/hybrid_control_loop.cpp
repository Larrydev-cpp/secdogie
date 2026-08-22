#include "hybrid_control_loop.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <sstream>


#if defined(_WIN32)
#include <uiautomation.h>
#endif

namespace secdogie::atlas {
namespace {

using Clock = std::chrono::steady_clock;

std::uint32_t ElapsedMs(Clock::time_point start) {
  return static_cast<std::uint32_t>(
      std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - start)
          .count());
}

Rect Inflate(const Rect& r, int pad) {
  return {r.x - pad, r.y - pad, r.w + pad * 2, r.h + pad * 2};
}

}  // namespace

const char* StepStatusName(StepStatus s) noexcept {
  switch (s) {
    case StepStatus::Pending: return "pending";
    case StepStatus::Perceiving: return "perceiving";
    case StepStatus::Targeting: return "targeting";
    case StepStatus::SnapshotBefore: return "snapshot-before";
    case StepStatus::Executing: return "executing";
    case StepStatus::SnapshotAfter: return "snapshot-after";
    case StepStatus::Verifying: return "verifying";
    case StepStatus::Confirmed: return "confirmed";
    case StepStatus::Retrying: return "retrying";
    case StepStatus::Fallback: return "fallback";
    case StepStatus::Blocked: return "blocked";
    case StepStatus::Failed: return "failed";
    case StepStatus::Passed: return "passed";
  }
  return "pending";
}

double PixelDiff::ChangedRatio(const Framebuffer& a, const Framebuffer& b) {
  if (a.width != b.width || a.height != b.height || a.bgra.size() != b.bgra.size()) {
    return 1.0;
  }
  const std::size_t n = a.bgra.size();
  if (n < 4) return 1.0;
  double acc = 0;
  std::size_t count = 0;
  for (std::size_t i = 0; i + 3 < n; i += 4) {
    const int alpha = std::max(a.bgra[i + 3], b.bgra[i + 3]);
    if (alpha < 8) continue;
    const double dr = std::abs(int(a.bgra[i + 2]) - int(b.bgra[i + 2]));
    const double dg = std::abs(int(a.bgra[i + 1]) - int(b.bgra[i + 1]));
    const double db = std::abs(int(a.bgra[i + 0]) - int(b.bgra[i + 0]));
    acc += (dr + dg + db) / (3.0 * 255.0);
    ++count;
  }
  return count ? acc / static_cast<double>(count) : 0.0;
}

std::string PixelDiff::Hash(const Framebuffer& fb) {
  std::uint32_t h = 5381;
  for (std::size_t i = 0; i < fb.bgra.size(); ++i) {
    h = ((h << 5) + h) ^ fb.bgra[i];
  }
  char buf[9];
  std::snprintf(buf, sizeof(buf), "%08x", h);
  return buf;
}

HybridControlLoop::HybridControlLoop(ProcessPerception perception, LoopConfig config)
    : perception_(std::move(perception)), config_(config) {
  capture_ = &HybridControlLoop::CaptureScreen;
  execute_ = &HybridControlLoop::ExecuteDefault;
}

Result<Framebuffer> HybridControlLoop::CaptureScreen(const Rect& r) {
#if !defined(_WIN32)
  (void)r;
  return PrivilegeError{PrivilegeCode::Unsupported, "GDI capture is Windows-only"};
#else
  if (!RectValid(r)) {
    return PrivilegeError{PrivilegeCode::Failed, "invalid capture rect"};
  }
  HDC screen = GetDC(nullptr);
  if (!screen) {
    return PrivilegeError{PrivilegeCode::Failed, "GetDC failed"};
  }
  HDC mem = CreateCompatibleDC(screen);
  if (!mem) {
    ReleaseDC(nullptr, screen);
    return PrivilegeError{PrivilegeCode::Failed, "CreateCompatibleDC failed"};
  }
  HBITMAP bmp = CreateCompatibleBitmap(screen, r.w, r.h);
  if (!bmp) {
    DeleteDC(mem);
    ReleaseDC(nullptr, screen);
    return PrivilegeError{PrivilegeCode::Failed, "CreateCompatibleBitmap failed"};
  }
  HGDIOBJ old = SelectObject(mem, bmp);
  BitBlt(mem, 0, 0, r.w, r.h, screen, r.x, r.y, SRCCOPY);

  BITMAPINFO bmi{};
  bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
  bmi.bmiHeader.biWidth = r.w;
  bmi.bmiHeader.biHeight = -r.h;  // top-down
  bmi.bmiHeader.biPlanes = 1;
  bmi.bmiHeader.biBitCount = 32;
  bmi.bmiHeader.biCompression = BI_RGB;

  Framebuffer fb;
  fb.width = r.w;
  fb.height = r.h;
  fb.bgra.resize(static_cast<std::size_t>(r.w) * r.h * 4);
  const int got = GetDIBits(mem, bmp, 0, r.h, fb.bgra.data(), &bmi, DIB_RGB_COLORS);

  SelectObject(mem, old);
  DeleteObject(bmp);
  DeleteDC(mem);
  ReleaseDC(nullptr, screen);

  if (got == 0) {
    return PrivilegeError{PrivilegeCode::Failed, "GetDIBits failed"};
  }
  return fb;
#endif
}

PrivilegeError HybridControlLoop::ExecuteDefault(const ControlNode& target,
                                                 const LoopAction& action) {
#if !defined(_WIN32)
  (void)target;
  (void)action;
  return PrivilegeError{PrivilegeCode::Unsupported, "UIA execute is Windows-only"};
#else
  if (action.kind == ActionKind::Read) {
    return PrivilegeError{PrivilegeCode::Ok, "read — no mutation"};
  }
  IUIAutomation* uia = nullptr;
  HRESULT hr = CoCreateInstance(CLSID_CUIAutomation, nullptr, CLSCTX_INPROC_SERVER,
                                IID_IUIAutomation, reinterpret_cast<void**>(&uia));
  if (FAILED(hr) || !uia) {
    return PrivilegeError{PrivilegeCode::Failed, "CUIAutomation unavailable"};
  }
  HWND hwnd = reinterpret_cast<HWND>(static_cast<uintptr_t>(target.hwnd));
  IUIAutomationElement* root = nullptr;
  hr = uia->ElementFromHandle(hwnd, &root);
  bool invoked = false;
  if (SUCCEEDED(hr) && root) {
    VARIANT v;
    VariantInit(&v);
    v.vt = VT_BSTR;
    v.bstrVal = SysAllocString(target.automation_id.c_str());
    IUIAutomationCondition* cond = nullptr;
    if (!target.automation_id.empty()) {
      uia->CreatePropertyCondition(UIA_AutomationIdPropertyId, v, &cond);
    }
    IUIAutomationElement* el = nullptr;
    if (cond) {
      root->FindFirst(TreeScope_Subtree, cond, &el);
      cond->Release();
    }
    VariantClear(&v);
    if (el) {
      IUnknown* pattern = nullptr;
      if (SUCCEEDED(el->GetCurrentPattern(UIA_InvokePatternId, &pattern)) && pattern) {
        static_cast<IUIAutomationInvokePattern*>(pattern)->Invoke();
        pattern->Release();
        invoked = true;
      } else if (SUCCEEDED(el->GetCurrentPattern(UIA_TogglePatternId, &pattern)) &&
                 pattern) {
        static_cast<IUIAutomationTogglePattern*>(pattern)->Toggle();
        pattern->Release();
        invoked = true;
      }
      el->Release();
    }
    root->Release();
  }
  uia->Release();
  if (invoked) return PrivilegeError{PrivilegeCode::Ok, "UIA invoke/toggle"};

  // Coordinate click fallback (still documented SendInput — not injection).
  INPUT in[2]{};
  in[0].type = INPUT_MOUSE;
  in[0].mi.dwFlags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTDOWN;
  in[1] = in[0];
  in[1].mi.dwFlags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTUP;
  const int cx = target.bounds.x + target.bounds.w / 2;
  const int cy = target.bounds.y + target.bounds.h / 2;
  const int sx = GetSystemMetrics(SM_CXSCREEN);
  const int sy = GetSystemMetrics(SM_CYSCREEN);
  if (sx <= 0 || sy <= 0) {
    return PrivilegeError{PrivilegeCode::Failed, "GetSystemMetrics screen size is 0"};
  }
  const int nx = cx * 65535 / sx;
  const int ny = cy * 65535 / sy;
  in[0].mi.dx = in[1].mi.dx = nx;
  in[0].mi.dy = in[1].mi.dy = ny;
  SendInput(2, in, sizeof(INPUT));
  return PrivilegeError{PrivilegeCode::Ok, "SendInput click fallback"};
#endif
}

LoopStep HybridControlLoop::Run(const LoopAction& action, SinkFn sink) {
  const auto t0 = Clock::now();
  LoopStep step;
  step.action_id = action.id;
  auto emit = [&](StepStatus st, const std::string& detail) {
    step.status = st;
    step.detail = detail;
    step.elapsed_ms = ElapsedMs(t0);
    if (sink) sink(step);
  };

  emit(StepStatus::Perceiving, "Reading UIA tree and process handles.");

  if (action.high_risk && !config_.operator_confirmed) {
    step.mode = PerceptionMode::Uia;
    emit(StepStatus::Blocked,
         "High-risk action is gated. Operator confirmation is required even "
         "under --auto. The vision model cannot confirm itself.");
    return step;
  }

  PerceptionSnapshot current = perception_.Snapshot();
  const ControlNode* found = ProcessPerception::Find(current.controls, action.selector);
  PerceptionMode mode = current.mode;

  if (!found) {
    emit(StepStatus::Fallback, "UIA miss — falling back to last-known / vision.");
    if (!config_.vision_fallback) {
      emit(StepStatus::Failed, "No UIA hit and vision fallback is disarmed.");
      return step;
    }
    found = ProcessPerception::Find(last_.controls, action.selector);
    mode = PerceptionMode::VisionFallback;
    step.mode = mode;
    if (!found) {
      emit(StepStatus::Failed, "No UIA hit and vision fallback could not resolve the selector.");
      return step;
    }
  }

  // Copy the node before we maybe move `current` into `last_`.
  ControlNode target = *found;
  if (!current.controls.empty()) {
    last_ = std::move(current);
  }

  step.mode = mode;
  step.target_name = target.name;
  step.target_bounds = target.bounds;
  emit(StepStatus::Targeting, mode == PerceptionMode::Uia ? "UIA target" : "Vision target");

  if (action.kind == ActionKind::Read) {
    emit(StepStatus::Passed, "Read-only: no mutation, no pixel-diff required.");
    return step;
  }

  if (!capture_) {
    emit(StepStatus::Failed, "No capture function wired.");
    return step;
  }

  emit(StepStatus::SnapshotBefore, "Capturing pre-action framebuffer.");
  const Rect cap = Inflate(target.bounds, config_.capture_pad_px);
  auto before = capture_(cap);
  if (!before) {
    emit(StepStatus::Failed, "pre-capture failed: " + before.error().detail);
    return step;
  }

  double last_diff = 0;
  for (int attempt = 0; attempt <= config_.max_retries; ++attempt) {
    emit(attempt == 0 ? StepStatus::Executing : StepStatus::Retrying,
         attempt == 0 ? "Invoke via UIA / click" : "Retry after insufficient pixel-diff");
    if (execute_) {
      const PrivilegeError ex = execute_(target, action);
      if (ex.code != PrivilegeCode::Ok && ex.code != PrivilegeCode::Stripped) {
        emit(StepStatus::Failed, "execute failed: " + ex.detail);
        return step;
      }
    }
    emit(StepStatus::SnapshotAfter, "Capturing post-action framebuffer.");
    auto after = capture_(cap);
    if (!after) {
      emit(StepStatus::Failed, "post-capture failed: " + after.error().detail);
      return step;
    }
    last_diff = PixelDiff::ChangedRatio(before.value(), after.value());
    step.diff_ratio = last_diff;
    std::ostringstream oss;
    oss.setf(std::ios::fixed);
    oss.precision(2);
    oss << "Pixel-diff " << (last_diff * 100.0) << "%";
    emit(StepStatus::Verifying, oss.str());
    if (last_diff >= config_.diff_threshold) {
      emit(StepStatus::Passed, "UI mutation verified.");
      return step;
    }
  }

  emit(StepStatus::Failed,
       "No visible mutation after retries. Action not committed as success.");
  return step;
}

}  // namespace secdogie::atlas
