#include "process_perception.h"

#include "unique_handle.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <sstream>

#if defined(_WIN32)
#include <oleauto.h>
#include <tlhelp32.h>
#include <uiautomation.h>
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")
#pragma comment(lib, "oleacc.lib")
#else
#include <cctype>
#include <cstdlib>
#include <dirent.h>
#include <fstream>
#include <iterator>
#include <unistd.h>
#endif

namespace secdogie::atlas {
namespace {

#if defined(_WIN32)
std::string Narrow(const std::wstring& w) {
  std::string s;
  s.resize(w.size());
  std::transform(w.begin(), w.end(), s.begin(), [](wchar_t c) {
    return static_cast<char>(c < 128 ? c : '?');
  });
  return s;
}
#endif

std::wstring FromUtf8(const std::string& s) {
  std::wstring w;
  w.reserve(s.size());
  for (unsigned char c : s) w.push_back(static_cast<wchar_t>(c));
  return w;
}

bool Ieq(const std::wstring& a, const std::wstring& b) {
  if (a.size() != b.size()) return false;
  for (std::size_t i = 0; i < a.size(); ++i) {
    wchar_t ca = a[i] >= L'A' && a[i] <= L'Z' ? static_cast<wchar_t>(a[i] + 32) : a[i];
    wchar_t cb = b[i] >= L'A' && b[i] <= L'Z' ? static_cast<wchar_t>(b[i] + 32) : b[i];
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

#if defined(_WIN32)
struct ComInit {
  ComInit() { CoInitializeEx(nullptr, COINIT_MULTITHREADED); }
  ~ComInit() { CoUninitialize(); }
};

Rect FromUiaRect(const RECT& r) {
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
  const Rect bounds = FromUiaRect(r);

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
  // Zero-bounds owner-drawn wrappers are still kept if they have a name/id
  // so Walk can attach children underneath them.
  return RectValid(bounds) || !out->name.empty() || !out->automation_id.empty();
}

// Real UIA tree walk. GetFirstChild / GetNextSibling, depth-capped, node-capped.
// Replaces the previous empty Walk() — that was a placeholder and is gone.
void Walk(IUIAutomationTreeWalker* walker, IUIAutomationElement* el, int depth,
          ControlNode* parent, std::uint32_t pid, std::uint64_t hwnd,
          std::size_t* remaining) {
  if (!walker || !el || !parent || !remaining || *remaining == 0) return;
  if (depth > ProcessPerception::kMaxTreeDepth) return;

  IUIAutomationElement* child = nullptr;
  HRESULT hr = walker->GetFirstChildElement(el, &child);
  if (FAILED(hr) || !child) return;

  while (child && *remaining > 0) {
    ControlNode node;
    const bool usable = ElementOf(child, &node, pid, hwnd);
    if (usable) {
      --(*remaining);
      Walk(walker, child, depth + 1, &node, pid, hwnd, remaining);
      parent->children.push_back(std::move(node));
    } else {
      Walk(walker, child, depth + 1, parent, pid, hwnd, remaining);
    }
    IUIAutomationElement* next = nullptr;
    walker->GetNextSiblingElement(child, &next);
    child->Release();
    child = next;
  }
  if (child) child->Release();
}

void FlattenFindAll(IUIAutomationElementArray* arr, ControlNode* parent,
                    std::uint32_t pid, std::uint64_t hwnd, std::size_t remaining) {
  if (!arr || !parent) return;
  int n = 0;
  arr->get_Length(&n);
  if (n < 0) n = 0;
  if (static_cast<std::size_t>(n) > remaining) n = static_cast<int>(remaining);
  for (int i = 0; i < n; ++i) {
    IUIAutomationElement* child = nullptr;
    arr->GetElement(i, &child);
    if (!child) continue;
    ControlNode node;
    if (ElementOf(child, &node, pid, hwnd)) {
      parent->children.push_back(std::move(node));
    }
    child->Release();
  }
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

std::vector<ListedProcess> ProcessPerception::ListProcesses() {
  std::vector<ListedProcess> out;
#if defined(_WIN32)
  HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
  if (snap == INVALID_HANDLE_VALUE) return out;
  UniqueHandle snap_h(snap);
  PROCESSENTRY32W pe{};
  pe.dwSize = sizeof(pe);
  if (Process32FirstW(snap_h.get(), &pe)) {
    do {
      ListedProcess p;
      p.pid = pe.th32ProcessID;
      p.image = pe.szExeFile;
      DWORD sid = 0;
      ProcessIdToSessionId(pe.th32ProcessID, &sid);
      p.session_id = sid;
      out.push_back(std::move(p));
      if (out.size() >= 8192) break;
    } while (Process32NextW(snap_h.get(), &pe));
  }
#else
  DIR* dir = opendir("/proc");
  if (!dir) return out;
  while (dirent* ent = readdir(dir)) {
    if (!ent->d_name[0] || !std::isdigit(static_cast<unsigned char>(ent->d_name[0]))) continue;
    char* end = nullptr;
    const unsigned long pid = std::strtoul(ent->d_name, &end, 10);
    if (!end || *end || pid == 0) continue;
    ListedProcess p;
    p.pid = static_cast<std::uint32_t>(pid);
    const std::string base = std::string("/proc/") + ent->d_name;
    {
      std::ifstream comm(base + "/comm");
      std::string name;
      if (comm) std::getline(comm, name);
      p.image = FromUtf8(name);
    }
    {
      std::ifstream cmd(base + "/cmdline", std::ios::binary);
      std::string raw((std::istreambuf_iterator<char>(cmd)),
                      std::istreambuf_iterator<char>());
      for (char& c : raw) {
        if (c == '\0') c = ' ';
      }
      while (!raw.empty() && raw.back() == ' ') raw.pop_back();
      if (!raw.empty()) p.cmdline = FromUtf8(raw);
    }
    {
      std::ifstream st(base + "/status");
      std::string line;
      while (st && std::getline(st, line)) {
        if (line.rfind("VmRSS:", 0) == 0) {
          unsigned long kb = 0;
          if (std::sscanf(line.c_str() + 6, "%lu", &kb) == 1) {
            p.rss_kb = kb;
          }
          break;
        }
      }
    }
    out.push_back(std::move(p));
    if (out.size() >= 8192) break;
  }
  closedir(dir);
#endif
  return out;
}

PerceptionSnapshot ProcessPerception::SnapshotLinux() {
  PerceptionSnapshot s;
  s.mode = PerceptionMode::Memory;
#if !defined(_WIN32)
  s.process.pid = static_cast<std::uint32_t>(getpid());
  char buf[4096];
  const ssize_t n = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
  if (n > 0) {
    buf[n] = 0;
    s.process.image = FromUtf8(std::string(buf, static_cast<std::size_t>(n)));
  }
  s.detail =
      "Linux: UI Automation is Windows-only. Snapshot reports this process so "
      "HybridControlLoop can InspectPid immediately (process_vm_readv).";
#else
  s.detail = "SnapshotLinux is not used on Windows";
#endif
  return s;
}

PerceptionSnapshot ProcessPerception::Snapshot() {
#if !defined(_WIN32)
  return SnapshotLinux();
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
  DWORD sid = 0;
  ProcessIdToSessionId(pid, &sid);
  snap.process.session_id = sid;

  wchar_t image[MAX_PATH]{};
  HANDLE proc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
  if (proc) {
    UniqueHandle ph(proc);
    DWORD n = MAX_PATH;
    if (QueryFullProcessImageNameW(ph.get(), 0, image, &n)) {
      snap.process.image.assign(image, n);
    }
  }

  IUIAutomation* uia = nullptr;
  HRESULT hr = CoCreateInstance(CLSID_CUIAutomation, nullptr, CLSCTX_INPROC_SERVER,
                                IID_IUIAutomation, reinterpret_cast<void**>(&uia));
  if (FAILED(hr) || !uia) {
    snap.detail = "CoCreateInstance(CUIAutomation) failed — vision/memory fallback";
    return snap;
  }

  IUIAutomationElement* root = nullptr;
  hr = uia->ElementFromHandle(fg, &root);
  if (FAILED(hr) || !root) {
    uia->Release();
    snap.detail = "ElementFromHandle failed — vision/memory fallback";
    return snap;
  }

  ControlNode window_node;
  const bool root_ok = ElementOf(root, &window_node, pid, snap.window.hwnd);
  if (root_ok) {
    IUIAutomationTreeWalker* walker = nullptr;
    hr = uia->get_ControlViewWalker(&walker);
    std::size_t remaining = ProcessPerception::kMaxTreeNodes;
    if (SUCCEEDED(hr) && walker) {
      Walk(walker, root, 0, &window_node, pid, snap.window.hwnd, &remaining);
      walker->Release();
      snap.detail = "UIA snapshot ok (ControlViewWalker tree)";
    } else {
      IUIAutomationCondition* true_cond = nullptr;
      uia->CreateTrueCondition(&true_cond);
      IUIAutomationElementArray* arr = nullptr;
      root->FindAll(TreeScope_Subtree, true_cond, &arr);
      if (true_cond) true_cond->Release();
      FlattenFindAll(arr, &window_node, pid, snap.window.hwnd, remaining);
      if (arr) arr->Release();
      snap.detail = "UIA snapshot ok (FindAll fallback — walker unavailable)";
    }
    snap.controls.push_back(std::move(window_node));
    snap.mode = PerceptionMode::Uia;
  } else {
    snap.detail = "root element had no usable name/bounds — vision/memory fallback";
  }
  root->Release();
  uia->Release();
#endif
  return snap;
}

}  // namespace secdogie::atlas
