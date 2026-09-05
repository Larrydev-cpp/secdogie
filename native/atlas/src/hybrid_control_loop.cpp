#include "hybrid_control_loop.h"
#include "utf.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <sstream>
#include <vector>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <objbase.h>
#include <UIAutomationClient.h>
#elif defined(__APPLE__)
#include <ApplicationServices/ApplicationServices.h>
#include <CoreGraphics/CoreGraphics.h>
#else
#include <unistd.h>
#endif

#if !defined(_WIN32)
#include <unistd.h>
#endif

#include "hybrid_tree.h"
#include "memory_inspector.h"

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

void Nap(int ms) {
  if (ms <= 0) return;
#if defined(_WIN32)
  Sleep(static_cast<DWORD>(ms));
#else
  usleep(static_cast<useconds_t>(ms) * 1000);
#endif
}

#if defined(__APPLE__)
bool WIeq(const std::wstring& a, const std::wstring& b) {
  if (a.size() != b.size()) return false;
  for (std::size_t i = 0; i < a.size(); ++i) {
    wchar_t ca = a[i] >= L'A' && a[i] <= L'Z' ? static_cast<wchar_t>(a[i] + 32) : a[i];
    wchar_t cb = b[i] >= L'A' && b[i] <= L'Z' ? static_cast<wchar_t>(b[i] + 32) : b[i];
    if (ca != cb) return false;
  }
  return true;
}

std::wstring CfWide(CFTypeRef v) {
  if (!v || CFGetTypeID(v) != CFStringGetTypeID()) return {};
  char buf[512];
  if (!CFStringGetCString(static_cast<CFStringRef>(v), buf, sizeof(buf), kCFStringEncodingUTF8)) {
    return {};
  }
  return Utf8ToWide(buf);
}

AXUIElementRef FindAx(AXUIElementRef el, const ControlNode& target, int depth) {
  if (!el || depth > ProcessPerception::kMaxTreeDepth) return nullptr;
  CFTypeRef ident = nullptr;
  CFTypeRef title = nullptr;
  CFTypeRef desc = nullptr;
  AXUIElementCopyAttributeValue(el, kAXIdentifierAttribute, &ident);
  AXUIElementCopyAttributeValue(el, kAXTitleAttribute, &title);
  AXUIElementCopyAttributeValue(el, kAXDescriptionAttribute, &desc);
  const std::wstring id = CfWide(ident);
  const std::wstring name = CfWide(title);
  const std::wstring d = CfWide(desc);
  if (ident) CFRelease(ident);
  if (title) CFRelease(title);
  if (desc) CFRelease(desc);

  bool hit = false;
  if (!target.automation_id.empty() && WIeq(id, target.automation_id)) {
    hit = true;
  } else if (target.automation_id.empty() && !target.name.empty() &&
             (WIeq(name, target.name) || WIeq(d, target.name))) {
    hit = true;
  }
  if (hit) {
    CFRetain(el);
    return el;
  }

  CFTypeRef children = nullptr;
  if (AXUIElementCopyAttributeValue(el, kAXChildrenAttribute, &children) != kAXErrorSuccess ||
      !children) {
    return nullptr;
  }
  if (CFGetTypeID(children) != CFArrayGetTypeID()) {
    CFRelease(children);
    return nullptr;
  }
  CFArrayRef arr = static_cast<CFArrayRef>(children);
  AXUIElementRef found = nullptr;
  const CFIndex n = CFArrayGetCount(arr);
  for (CFIndex i = 0; i < n && !found; ++i) {
    AXUIElementRef child = static_cast<AXUIElementRef>(
        const_cast<void*>(CFArrayGetValueAtIndex(arr, i)));
    found = FindAx(child, target, depth + 1);
  }
  CFRelease(children);
  return found;
}

PrivilegeError ExecuteAxPress(const ControlNode& target, const LoopAction& action) {
  if (action.kind == ActionKind::Read) {
    return PrivilegeError{PrivilegeCode::Ok, "read — no mutation"};
  }
  if (target.pid == 0) {
    return PrivilegeError{PrivilegeCode::Failed,
                          "macOS AX press needs a pid. HID/CGEvent/IOHID refused."};
  }
  if (!AXIsProcessTrusted()) {
    // Own-process AX can still work; keep going but remember the hint.
  }
  AXUIElementRef app = AXUIElementCreateApplication(static_cast<pid_t>(target.pid));
  if (!app) {
    return PrivilegeError{PrivilegeCode::Failed,
                          "AXUIElementCreateApplication failed. HID/CGEvent refused."};
  }
  AXUIElementRef found = FindAx(app, target, 0);
  if (!found) {
    CFRelease(app);
    const char* trust = AXIsProcessTrusted() ? "AX miss" : "Accessibility not granted";
    return PrivilegeError{PrivilegeCode::Failed,
                          std::string("macOS ") + trust +
                              " — AXPress only, HID/CGEvent/IOHID refused."};
  }
  AXError err = AXUIElementPerformAction(found, kAXPressAction);
  if (err != kAXErrorSuccess) {
    err = AXUIElementPerformAction(found, kAXConfirmAction);
  }
  CFRelease(found);
  CFRelease(app);
  if (err != kAXErrorSuccess) {
    return PrivilegeError{PrivilegeCode::Failed,
                          "AXPress/AXConfirm failed. HID/CGEvent/IOHID refused."};
  }
  return PrivilegeError{PrivilegeCode::Ok, "AX press (no HID)"};
}

Result<Framebuffer> CaptureCgWindow(const Rect& r) {
  if (!RectValid(r)) {
    return PrivilegeError{PrivilegeCode::Failed, "invalid capture rect"};
  }
  const CGRect rect = CGRectMake(r.x, r.y, r.w, r.h);
  CGImageRef img = CGWindowListCreateImage(
      rect, kCGWindowListOptionOnScreenOnly, kCGNullWindowID, kCGWindowImageDefault);
  if (!img) {
    return PrivilegeError{PrivilegeCode::Failed,
                          "CGWindowListCreateImage failed (grant Screen Recording). "
                          "HID/CGEvent capture refused."};
  }
  const size_t w = CGImageGetWidth(img);
  const size_t h = CGImageGetHeight(img);
  if (w == 0 || h == 0 || w > 8192 || h > 8192) {
    CGImageRelease(img);
    return PrivilegeError{PrivilegeCode::Failed,
                          "CGWindow image empty or too large (Screen Recording?). "
                          "HID/CGEvent refused."};
  }
  Framebuffer fb;
  fb.width = static_cast<std::int32_t>(w);
  fb.height = static_cast<std::int32_t>(h);
  fb.bgra.resize(w * h * 4);
  CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
  CGContextRef ctx = CGBitmapContextCreate(
      fb.bgra.data(), w, h, 8, w * 4, cs,
      kCGImageAlphaPremultipliedFirst | kCGBitmapByteOrder32Little);
  if (!ctx) {
    if (cs) CGColorSpaceRelease(cs);
    CGImageRelease(img);
    return PrivilegeError{PrivilegeCode::Failed, "CGBitmapContextCreate failed"};
  }
  CGContextDrawImage(ctx, CGRectMake(0, 0, static_cast<CGFloat>(w), static_cast<CGFloat>(h)), img);
  CGContextRelease(ctx);
  CGColorSpaceRelease(cs);
  CGImageRelease(img);
  return fb;
}
#endif

}  // namespace

const char* MutationBackendName() noexcept {
#if defined(_WIN32)
  return "uia+sendinput";
#elif defined(__APPLE__)
  return "ax-press";
#else
  return "none";
#endif
}

bool MutationUsesHid() noexcept {
#if defined(_WIN32)
  return true;
#else
  return false;
#endif
}

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
    const int alpha = (std::max)(a.bgra[i + 3], b.bgra[i + 3]);
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
#if defined(_WIN32)
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
#elif defined(__APPLE__)
  return CaptureCgWindow(r);
#else
  (void)r;
  return PrivilegeError{PrivilegeCode::Unsupported,
                        "Linux: no HID / GDI capture. Viewport is process-memory DIB."};
#endif
}

PrivilegeError HybridControlLoop::ExecuteDefault(const ControlNode& target,
                                                 const LoopAction& action) {
#if defined(_WIN32)
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
#elif defined(__APPLE__)
  return ExecuteAxPress(target, action);
#else
  (void)target;
  (void)action;
  return PrivilegeError{PrivilegeCode::Unsupported,
                        "Linux: no HID mutate, no AT-SPI. Read-only inspect."};
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

  PerceptionSnapshot current = config_.target_pid
                                   ? perception_.SnapshotPid(config_.target_pid)
                                   : perception_.Snapshot();
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
  }

  std::vector<ControlNode> memory_controls;
  if (!found && config_.memory_fallback) {
    emit(StepStatus::Fallback, "last-known miss — read-only process memory inspect.");
    std::uint32_t pid = current.process.pid;
    if (pid == 0) pid = last_.process.pid;
    if (pid == 0) {
      emit(StepStatus::Failed, "No UIA hit and no PID to inspect.");
      return step;
    }
    InspectConfig ic;
    ic.max_strings = 8192;
    ic.max_bytes = 32ull * 1024ull * 1024ull;
    Result<InspectSnapshot> mem{PrivilegeError{PrivilegeCode::Failed, "pending"}};
    for (int i = 0; i < 3; ++i) {
      mem = InspectPid(pid, ic);
      if (mem) break;
      emit(StepStatus::Retrying, "memory inspect jitter, retry " + std::to_string(i + 1));
      Nap(80 << i);
    }
    if (!mem) {
      found = ProcessPerception::Find(last_.controls, action.selector);
      if (found) {
        emit(StepStatus::Fallback, "inspect refused after retries — last-known tree (isolated)");
        memory_controls = last_.controls;
        mode = PerceptionMode::Memory;
        step.mode = mode;
      } else {
        emit(StepStatus::Failed, "memory inspect refused: " + mem.error().detail);
        return step;
      }
    } else {
    const std::vector<ControlNode>& seed =
        !current.controls.empty() ? current.controls : last_.controls;
    const std::vector<HybridNode> hybrid =
        FuseTree(seed, mem.value().strings, mem.value().strings.size());
    memory_controls = HybridAsControls(hybrid);
    found = ProcessPerception::Find(memory_controls, action.selector);
    if (!found) {
      // Selector may match a raw hit even if fusion capped/deduped names.
      for (const auto& hit : mem.value().strings) {
        ControlNode n = MemoryHitAsControl(hit);
        memory_controls.push_back(std::move(n));
      }
      found = ProcessPerception::Find(memory_controls, action.selector);
    }
    mode = PerceptionMode::Memory;
    step.mode = mode;
    }
  }

  if (!found) {
    emit(StepStatus::Failed, "No UIA hit, last-known miss, memory inspect did not resolve the selector.");
    return step;
  }

  // Copy the node before we maybe move `current` into `last_`.
  ControlNode target = *found;
  if (!current.controls.empty()) {
    last_ = std::move(current);
  } else if (current.process.pid != 0) {
    last_.process = current.process;
  }

  step.mode = mode;
  step.target_name = target.name;
  step.target_bounds = target.bounds;
  emit(StepStatus::Targeting,
       mode == PerceptionMode::Uia
           ? "UIA target"
           : (mode == PerceptionMode::Memory ? "Memory target" : "Vision target"));

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
  Result<Framebuffer> before{PrivilegeError{PrivilegeCode::Failed, "pending"}};
  for (int i = 0; i < 3; ++i) {
    before = capture_(cap);
    if (before) break;
    Nap(80 << i);
  }
  if (!before) {
    emit(StepStatus::Failed, "pre-capture failed: " + before.error().detail);
    return step;
  }

  double last_diff = 0;
  for (int attempt = 0; attempt <= config_.max_retries; ++attempt) {
    emit(attempt == 0 ? StepStatus::Executing : StepStatus::Retrying,
         attempt == 0 ? (std::string("mutate via ") + MutationBackendName())
                      : "Retry after insufficient pixel-diff");
    if (execute_) {
      const PrivilegeError ex = execute_(target, action);
      if (ex.code != PrivilegeCode::Ok && ex.code != PrivilegeCode::Stripped) {
        emit(StepStatus::Failed, "execute failed: " + ex.detail);
        return step;
      }
    }
    emit(StepStatus::SnapshotAfter, "Capturing post-action framebuffer.");
    Result<Framebuffer> after{PrivilegeError{PrivilegeCode::Failed, "pending"}};
    for (int i = 0; i < 3; ++i) {
      after = capture_(cap);
      if (after) break;
      Nap(40 << i);
    }
    if (!after) {
      emit(StepStatus::Retrying, "post-capture jitter: " + after.error().detail);
      continue;
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
