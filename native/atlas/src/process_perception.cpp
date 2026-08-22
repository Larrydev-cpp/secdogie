#include "process_perception.h"

#include <algorithm>
#include <sstream>

#if defined(_WIN32)
#include <oleauto.h>
#include <tlhelp32.h>
#include <uiautomation.h>
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")
#pragma comment(lib, "oleacc.lib")
#endif

namespace secdogie::atlas {
namespace {

std::string Narrow(const std::wstring& w) {
  std::string s;
  s.resize(w.size());
  std::transform(w.begin(), w.end(), s.begin(), [](wchar_t c) {
    return static_cast<char>(c < 128 ? c : '?');
  });
  return s;
}

#if defined(_WIN32)
struct ComInit {
  ComInit() { CoInitializeEx(nullptr, COINIT_MULTITHREADED); }
  ~ComInit() { CoUninitialize(); }
};

Rect FromUia(const RECT& r) {
  Rect o;
  o.x = r.left;
  o.y = r.top;
  o.w = r.right - r.left;
  o.h = r.bottom - r.top;
  return o;
}

std::wstring BstrToW(BSTR b) { return b ? std::wstring(b, SysStringLen(b)) : std::wstring(); }

ControlRole MapType(CONTROLTYPEID t) {
  switch (t) {
    case UIA_WindowControlTypeId: return ControlRole::Window;
    case UIA_PaneControlTypeId: return ControlRole::Pane;
    case UIA_ButtonControlTypeId: return ControlRole::Button;
    case UIA_TabItemControlTypeId: return ControlRole::TabItem;
    case UIA_TreeItemControlTypeId: return ControlRole::TreeItem;
    case UIA_EditControlTypeId: return ControlRole::Edit;
    case UIA_TextControlTypeId: return ControlRole::Text;
    case UIA_MenuItemControlTypeId: return ControlRole::MenuItem;
    case UIA_ToolBarControlTypeId: return ControlRole::ToolBar;
    default: return ControlRole::Custom;
  }
}

bool ElementOf(IUIAutomationElement* el, ControlNode* out, std::uint32_t pid,
               std::uint64_t hwnd) {
  if (!el || !out) return false;
  CONTROLTYPEID type = 0;
  el->get_CurrentControlType(&type);
  RECT r{};
  el->get_CurrentBoundingRectangle(&r);
  const Rect bounds = FromUia(r);
  if (!RectValid(bounds)) return false;

  BSTR name = nullptr;
  BSTR auto_id = nullptr;
  BOOL enabled = TRUE;
  el->get_CurrentName(&name);
  el->get_CurrentAutomationId(&auto_id);
  el->get_CurrentIsEnabled(&enabled);

  out->role = MapType(type);
  out->name = BstrToW(name);
  out->automation_id = BstrToW(auto_id);
  out->bounds = bounds;
  out->pid = pid;
  out->hwnd = hwnd;
  out->enabled = enabled != FALSE;
  out->id = Narrow(out->automation_id.empty() ? out->name : out->automation_id);

  if (name) SysFreeString(name);
  if (auto_id) SysFreeString(auto_id);
  return true;
}

void Walk(IUIAutomationElement* el, int depth, ControlNode* parent,
          std::uint32_t pid, std::uint64_t hwnd) {
  if (!el || depth > ProcessPerception::kMaxTreeDepth) return;
  IUIAutomationTreeWalker* walker = nullptr;
  IUIAutomation* uia = nullptr;
  // Walker is created by the caller via a thread-local; we instead use
  // FindAll on the raw view for a bounded snapshot.
  (void)walker;
  (void)uia;
  (void)parent;
  (void)pid;
  (void)hwnd;
}

BOOL CALLBACK EnumProc(HWND hwnd, LPARAM lp) {
  auto* out = reinterpret_cast<std::vector<WindowInfo>*>(lp);
  if (!IsWindowVisible(hwnd)) return TRUE;
  wchar_t title[512];
  wchar_t cls[256];
  GetWindowTextW(hwnd, title, 512);
  GetClassNameW(hwnd, cls, 256);
  if (title[0] == 0) return TRUE;
  RECT r{};
  GetWindowRect(hwnd, &r);
  DWORD pid = 0;
  GetWindowThreadProcessId(hwnd, &pid);
  WindowInfo w;
  w.hwnd = reinterpret_cast<std::uint64_t>(hwnd);
  w.pid = pid;
  w.title = title;
  w.class_name = cls;
  w.bounds = {r.left, r.top, r.right - r.left, r.bottom - r.top};
  w.visible = true;
  out->push_back(std::move(w));
  return TRUE;
}
#endif

bool Ieq(const std::wstring& a, const std::wstring& b) {
  if (a.size() != b.size()) return false;
  for (std::size_t i = 0; i < a.size(); ++i) {
    wchar_t ca = a[i] >= L'A' && a[i] <= L'Z' ? a[i] + 32 : a[i];
    wchar_t cb = b[i] >= L'A' && b[i] <= L'Z' ? b[i] + 32 : b[i];
    if (ca != cb) return false;
  }
  return true;
}

bool NodeMatches(const ControlNode& n, const Selector& s) {
  if (!s.automation_id.empty() && !Ieq(n.automation_id, s.automation_id)) {
    return false;
  }
  if (!s.name.empty() && !Ieq(n.name, s.name)) return false;
  if (s.has_role && n.role != s.role) return false;
  return !s.automation_id.empty() || !s.name.empty() || s.has_role;
}

}  // namespace

const char* RoleName(ControlRole r) noexcept {
  switch (r) {
    case ControlRole::Window: return "Window";
    case ControlRole::Pane: return "Pane";
    case ControlRole::Button: return "Button";
    case ControlRole::TabItem: return "TabItem";
    case ControlRole::TreeItem: return "TreeItem";
    case ControlRole::Edit: return "Edit";
    case ControlRole::Text: return "Text";
    case ControlRole::MenuItem: return "MenuItem";
    case ControlRole::ToolBar: return "ToolBar";
    case ControlRole::Custom: return "Custom";
  }
  return "Custom";
}

ControlRole RoleFromUiaType(int control_type) noexcept {
#if defined(_WIN32)
  return MapType(static_cast<CONTROLTYPEID>(control_type));
#else
  (void)control_type;
  return ControlRole::Custom;
#endif
}

void ProcessPerception::Flatten(const std::vector<ControlNode>& roots,
                                std::vector<const ControlNode*>& out) {
  for (const auto& n : roots) {
    out.push_back(&n);
    Flatten(n.children, out);
  }
}

const ControlNode* ProcessPerception::Find(const std::vector<ControlNode>& roots,
                                           const Selector& selector) {
  std::vector<const ControlNode*> flat;
  Flatten(roots, flat);
  for (const ControlNode* n : flat) {
    if (NodeMatches(*n, selector)) return n;
  }
  return nullptr;
}

std::vector<WindowInfo> ProcessPerception::ListWindows() {
  std::vector<WindowInfo> out;
#if defined(_WIN32)
  EnumWindows(EnumProc, reinterpret_cast<LPARAM>(&out));
#endif
  return out;
}

PerceptionSnapshot ProcessPerception::Snapshot() {
#if !defined(_WIN32)
  PerceptionSnapshot s;
  s.mode = PerceptionMode::VisionFallback;
  s.detail = "UI Automation is Windows-only; caller should use vision fallback.";
  return s;
#else
  return SnapshotWindows();
#endif
}

PerceptionSnapshot ProcessPerception::SnapshotWindows() {
  PerceptionSnapshot snap;
  snap.mode = PerceptionMode::VisionFallback;
  snap.detail = "UIA unavailable";

#if defined(_WIN32)
  ComInit com;
  HWND fg = GetForegroundWindow();
  if (!fg) {
    snap.detail = "no foreground window";
    return snap;
  }
  DWORD pid = 0;
  GetWindowThreadProcessId(fg, &pid);
  wchar_t title[512]{};
  wchar_t cls[256]{};
  GetWindowTextW(fg, title, 512);
  GetClassNameW(fg, cls, 256);
  RECT wr{};
  GetWindowRect(fg, &wr);
  snap.window.hwnd = reinterpret_cast<std::uint64_t>(fg);
  snap.window.pid = pid;
  snap.window.title = title;
  snap.window.class_name = cls;
  snap.window.bounds = {wr.left, wr.top, wr.right - wr.left, wr.bottom - wr.top};
  snap.window.visible = true;
  snap.process.pid = pid;
  snap.process.session_id = 0;
  ProcessIdToSessionId(pid, reinterpret_cast<DWORD*>(&snap.process.session_id));

  IUIAutomation* uia = nullptr;
  HRESULT hr = CoCreateInstance(CLSID_CUIAutomation, nullptr, CLSCTX_INPROC_SERVER,
                                IID_IUIAutomation, reinterpret_cast<void**>(&uia));
  if (FAILED(hr) || !uia) {
    snap.detail = "CoCreateInstance(CUIAutomation) failed — vision fallback";
    return snap;
  }

  IUIAutomationElement* root = nullptr;
  hr = uia->ElementFromHandle(fg, &root);
  if (FAILED(hr) || !root) {
    uia->Release();
    snap.detail = "ElementFromHandle failed — vision fallback";
    return snap;
  }

  IUIAutomationCondition* true_cond = nullptr;
  uia->CreateTrueCondition(&true_cond);
  IUIAutomationElementArray* arr = nullptr;
  hr = root->FindAll(TreeScope_Subtree, true_cond, &arr);
  if (true_cond) true_cond->Release();

  ControlNode window_node;
  if (ElementOf(root, &window_node, pid, snap.window.hwnd)) {
    if (arr) {
      int n = 0;
      arr->get_Length(&n);
      if (n > 4000) n = 4000;  // hard cap, pathological trees
      for (int i = 0; i < n; ++i) {
        IUIAutomationElement* child = nullptr;
        arr->GetElement(i, &child);
        if (!child) continue;
        ControlNode node;
        if (ElementOf(child, &node, pid, snap.window.hwnd)) {
          window_node.children.push_back(std::move(node));
        }
        child->Release();
      }
      arr->Release();
    }
    snap.controls.push_back(std::move(window_node));
    snap.mode = PerceptionMode::Uia;
    snap.detail = "UIA snapshot ok";
  } else {
    snap.detail = "root element had no usable bounds — vision fallback";
  }
  root->Release();
  uia->Release();
#endif
  return snap;
}

}  // namespace secdogie::atlas
