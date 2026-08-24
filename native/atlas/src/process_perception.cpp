#include "process_perception.h"

#include "unique_handle.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <sstream>
#include <vector>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <oleauto.h>
#include <psapi.h>
#include <tlhelp32.h>
#include <uiautomation.h>
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")
#pragma comment(lib, "oleacc.lib")
#pragma comment(lib, "psapi.lib")
#else
#include <cctype>
#include <cstdlib>
#include <dirent.h>
#include <fstream>
#include <iterator>
#include <unistd.h>
#if defined(__APPLE__)
#include <ApplicationServices/ApplicationServices.h>
#include <cstring>
#include <libproc.h>
#include <sys/sysctl.h>
#include <sys/syslimits.h>
#endif
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
#elif defined(__APPLE__)
  CFArrayRef arr = CGWindowListCopyWindowInfo(
      kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements, kCGNullWindowID);
  if (!arr) return out;
  const CFIndex n = CFArrayGetCount(arr);
  for (CFIndex i = 0; i < n; ++i) {
    CFDictionaryRef d = static_cast<CFDictionaryRef>(CFArrayGetValueAtIndex(arr, i));
    if (!d) continue;
    WindowInfo w;
    CFNumberRef pidn = static_cast<CFNumberRef>(CFDictionaryGetValue(d, kCGWindowOwnerPID));
    int pid = 0;
    if (pidn) CFNumberGetValue(pidn, kCFNumberIntType, &pid);
    w.pid = static_cast<std::uint32_t>(pid);
    CFNumberRef num = static_cast<CFNumberRef>(CFDictionaryGetValue(d, kCGWindowNumber));
    int64_t hwnd = 0;
    if (num) CFNumberGetValue(num, kCFNumberSInt64Type, &hwnd);
    w.hwnd = static_cast<std::uint64_t>(hwnd);
    CFStringRef title = static_cast<CFStringRef>(CFDictionaryGetValue(d, kCGWindowName));
    if (title) {
      char buf[512];
      if (CFStringGetCString(title, buf, sizeof(buf), kCFStringEncodingUTF8)) {
        w.title = FromUtf8(buf);
      }
    }
    CFStringRef owner = static_cast<CFStringRef>(CFDictionaryGetValue(d, kCGWindowOwnerName));
    if (owner) {
      char buf[256];
      if (CFStringGetCString(owner, buf, sizeof(buf), kCFStringEncodingUTF8)) {
        w.class_name = FromUtf8(buf);
      }
    }
    CFDictionaryRef b = static_cast<CFDictionaryRef>(CFDictionaryGetValue(d, kCGWindowBounds));
    if (b) {
      CGRect r{};
      CGRectMakeWithDictionaryRepresentation(b, &r);
      w.bounds = {static_cast<std::int32_t>(r.origin.x), static_cast<std::int32_t>(r.origin.y),
                  static_cast<std::int32_t>(r.size.width), static_cast<std::int32_t>(r.size.height)};
    }
    w.visible = true;
    if (!w.title.empty()) out.push_back(std::move(w));
  }
  CFRelease(arr);
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
      HANDLE ph = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pe.th32ProcessID);
      if (ph) {
        UniqueHandle h(ph);
        PROCESS_MEMORY_COUNTERS pmc{};
        if (GetProcessMemoryInfo(h.get(), &pmc, sizeof(pmc))) {
          p.rss_kb = pmc.WorkingSetSize / 1024;
        }
        using NtQip = LONG(WINAPI*)(HANDLE, ULONG, PVOID, ULONG, PULONG);
        HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
        auto ntq = ntdll ? reinterpret_cast<NtQip>(
                               GetProcAddress(ntdll, "NtQueryInformationProcess"))
                         : nullptr;
        if (ntq) {
          ULONG need = 0;
          ntq(h.get(), 60, nullptr, 0, &need);
          if (need > 0 && need <= 65536) {
            std::vector<unsigned char> buf(need);
            if (ntq(h.get(), 60, buf.data(), need, &need) >= 0) {
              struct UStr {
                unsigned short length;
                unsigned short maximum_length;
                wchar_t* buffer;
              };
              auto* us = reinterpret_cast<UStr*>(buf.data());
              auto* begin = reinterpret_cast<unsigned char*>(buf.data());
              auto* pb = reinterpret_cast<unsigned char*>(us->buffer);
              const wchar_t* src = nullptr;
              if (us->buffer && us->length >= 2 && pb >= begin &&
                  pb + us->length <= begin + buf.size()) {
                src = us->buffer;
              } else if (us->length >= 2 && sizeof(UStr) + us->length <= buf.size()) {
                src = reinterpret_cast<const wchar_t*>(buf.data() + sizeof(UStr));
              }
              if (src) p.cmdline.assign(src, us->length / sizeof(wchar_t));
            }
          }
        }
      }
      out.push_back(std::move(p));
      if (out.size() >= 8192) break;
    } while (Process32NextW(snap_h.get(), &pe));
  }
#elif defined(__APPLE__)
  int mib[4] = {CTL_KERN, KERN_PROC, KERN_PROC_ALL, 0};
  std::size_t len = 0;
  if (sysctl(mib, 4, nullptr, &len, nullptr, 0) != 0 || len == 0) return out;
  std::vector<char> buf(len);
  if (sysctl(mib, 4, buf.data(), &len, nullptr, 0) != 0) return out;
  const auto* kp = reinterpret_cast<kinfo_proc*>(buf.data());
  const std::size_t count = len / sizeof(kinfo_proc);
  for (std::size_t i = 0; i < count && out.size() < 8192; ++i) {
    ListedProcess p;
    p.pid = static_cast<std::uint32_t>(kp[i].kp_proc.p_pid);
    if (p.pid == 0) continue;
    p.image = FromUtf8(kp[i].kp_proc.p_comm);
    char path[PROC_PIDPATHINFO_MAXSIZE]{};
    if (proc_pidpath(static_cast<int>(p.pid), path, sizeof(path)) > 0) {
      p.cmdline = FromUtf8(path);
    }
    proc_taskinfo ti{};
    if (proc_pidinfo(static_cast<int>(p.pid), PROC_PIDTASKINFO, 0, &ti, sizeof(ti)) ==
        static_cast<int>(sizeof(ti))) {
      p.rss_kb = static_cast<std::uint64_t>(ti.pti_resident_size / 1024);
    }
    out.push_back(std::move(p));
  }
#elif defined(__linux__)
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
#if defined(__linux__)
  s.process.pid = static_cast<std::uint32_t>(getpid());
  char buf[4096];
  const ssize_t n = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
  if (n > 0) {
    buf[n] = 0;
    s.process.image = FromUtf8(std::string(buf, static_cast<std::size_t>(n)));
  }
  s.detail =
      "Linux: window tree needs a compositor (AT-SPI not linked). "
      "Memory inspect of this pid uses process_vm_readv.";
#else
  s.detail = "SnapshotLinux is Linux-only";
#endif
  return s;
}

#if defined(__APPLE__)
ControlRole AxRole(CFStringRef role) {
  if (!role) return ControlRole::Custom;
  char buf[128];
  if (!CFStringGetCString(role, buf, sizeof(buf), kCFStringEncodingUTF8)) {
    return ControlRole::Custom;
  }
  if (std::strstr(buf, "Button")) return ControlRole::Button;
  if (std::strstr(buf, "Window")) return ControlRole::Window;
  if (std::strstr(buf, "Text") || std::strstr(buf, "Static")) return ControlRole::Text;
  if (std::strstr(buf, "TextField") || std::strstr(buf, "Edit")) return ControlRole::Edit;
  if (std::strstr(buf, "Menu")) return ControlRole::MenuItem;
  if (std::strstr(buf, "Toolbar")) return ControlRole::ToolBar;
  if (std::strstr(buf, "Tab")) return ControlRole::TabItem;
  return ControlRole::Custom;
}

void WalkAx(AXUIElementRef el, int depth, ControlNode* parent, std::uint32_t pid,
            std::uint64_t hwnd, std::size_t* remaining) {
  if (!el || !parent || !remaining || *remaining == 0) return;
  if (depth > ProcessPerception::kMaxTreeDepth) return;
  CFTypeRef children = nullptr;
  if (AXUIElementCopyAttributeValue(el, kAXChildrenAttribute, &children) != kAXErrorSuccess ||
      !children) {
    return;
  }
  if (CFGetTypeID(children) != CFArrayGetTypeID()) {
    CFRelease(children);
    return;
  }
  CFArrayRef arr = static_cast<CFArrayRef>(children);
  const CFIndex n = CFArrayGetCount(arr);
  for (CFIndex i = 0; i < n && *remaining > 0; ++i) {
    AXUIElementRef child = static_cast<AXUIElementRef>(
        const_cast<void*>(CFArrayGetValueAtIndex(arr, i)));
    if (!child) continue;
    ControlNode node;
    CFTypeRef role = nullptr;
    CFTypeRef title = nullptr;
    CFTypeRef ident = nullptr;
    AXUIElementCopyAttributeValue(child, kAXRoleAttribute, &role);
    AXUIElementCopyAttributeValue(child, kAXTitleAttribute, &title);
    AXUIElementCopyAttributeValue(child, kAXIdentifierAttribute, &ident);
    node.role = role && CFGetTypeID(role) == CFStringGetTypeID()
                    ? AxRole(static_cast<CFStringRef>(role))
                    : ControlRole::Custom;
    if (title && CFGetTypeID(title) == CFStringGetTypeID()) {
      char buf[512];
      if (CFStringGetCString(static_cast<CFStringRef>(title), buf, sizeof(buf),
                             kCFStringEncodingUTF8)) {
        node.name = FromUtf8(buf);
      }
    }
    if (ident && CFGetTypeID(ident) == CFStringGetTypeID()) {
      char buf[256];
      if (CFStringGetCString(static_cast<CFStringRef>(ident), buf, sizeof(buf),
                             kCFStringEncodingUTF8)) {
        node.automation_id = FromUtf8(buf);
      }
    }
    node.pid = pid;
    node.hwnd = hwnd;
    {
      const std::wstring& src = node.automation_id.empty() ? node.name : node.automation_id;
      node.id.resize(src.size());
      for (std::size_t i = 0; i < src.size(); ++i) {
        node.id[i] = src[i] < 128 ? static_cast<char>(src[i]) : '?';
      }
    }
    if (role) CFRelease(role);
    if (title) CFRelease(title);
    if (ident) CFRelease(ident);
    --(*remaining);
    WalkAx(child, depth + 1, &node, pid, hwnd, remaining);
    parent->children.push_back(std::move(node));
  }
  CFRelease(children);
}
#endif

PerceptionSnapshot ProcessPerception::SnapshotDarwin() {
  PerceptionSnapshot s;
  s.mode = PerceptionMode::Memory;
#if defined(__APPLE__)
  s.process.pid = static_cast<std::uint32_t>(getpid());
  char path[PROC_PIDPATHINFO_MAXSIZE]{};
  if (proc_pidpath(static_cast<int>(s.process.pid), path, sizeof(path)) > 0) {
    s.process.image = FromUtf8(path);
  }
  s.detail = "macOS: AXUIElement of this process; mach_vm_read on miss.";
#else
  s.detail = "SnapshotDarwin is macOS-only";
#endif
  return s;
}

PerceptionSnapshot ProcessPerception::Snapshot() {
#if defined(_WIN32)
  HWND fg = GetForegroundWindow();
  DWORD pid = 0;
  if (fg) GetWindowThreadProcessId(fg, &pid);
  if (pid == 0) pid = GetCurrentProcessId();
  return SnapshotPid(static_cast<std::uint32_t>(pid));
#elif defined(__APPLE__)
  return SnapshotPid(static_cast<std::uint32_t>(getpid()));
#else
  return SnapshotLinux();
#endif
}

PerceptionSnapshot ProcessPerception::SnapshotPid(std::uint32_t pid) {
#if defined(__linux__)
  PerceptionSnapshot s = SnapshotLinux();
  s.process.pid = pid;
  s.detail =
      "Linux: memory inspect of the requested pid uses process_vm_readv. "
      "Window tree is compositor-dependent (AT-SPI not linked).";
  return s;
#elif defined(__APPLE__)
  PerceptionSnapshot snap;
  snap.mode = PerceptionMode::Memory;
  snap.process.pid = pid;
  char path[PROC_PIDPATHINFO_MAXSIZE]{};
  if (proc_pidpath(static_cast<int>(pid), path, sizeof(path)) > 0) {
    snap.process.image = FromUtf8(path);
  }
  snap.detail = "macOS: no AX windows for pid — mach_vm_read is primary";

  AXUIElementRef app = AXUIElementCreateApplication(static_cast<pid_t>(pid));
  if (!app) return snap;
  CFTypeRef wins = nullptr;
  const AXError err = AXUIElementCopyAttributeValue(app, kAXWindowsAttribute, &wins);
  if (err != kAXErrorSuccess || !wins || CFGetTypeID(wins) != CFArrayGetTypeID()) {
    snap.detail =
        "macOS: AXUIElementCreateApplication ok but no windows "
        "(grant Accessibility in Privacy & Security). Memory inspect still runs.";
    if (wins) CFRelease(wins);
    CFRelease(app);
    return snap;
  }
  CFArrayRef arr = static_cast<CFArrayRef>(wins);
  std::size_t remaining = ProcessPerception::kMaxTreeNodes;
  const CFIndex n = CFArrayGetCount(arr);
  for (CFIndex i = 0; i < n && remaining > 0; ++i) {
    AXUIElementRef win = static_cast<AXUIElementRef>(
        const_cast<void*>(CFArrayGetValueAtIndex(arr, i)));
    if (!win) continue;
    ControlNode window_node;
    window_node.role = ControlRole::Window;
    window_node.pid = pid;
    CFTypeRef title = nullptr;
    AXUIElementCopyAttributeValue(win, kAXTitleAttribute, &title);
    if (title && CFGetTypeID(title) == CFStringGetTypeID()) {
      char buf[512];
      if (CFStringGetCString(static_cast<CFStringRef>(title), buf, sizeof(buf),
                             kCFStringEncodingUTF8)) {
        window_node.name = FromUtf8(buf);
      }
    }
    if (title) CFRelease(title);
    {
      const std::wstring& src = window_node.name;
      window_node.id.resize(src.size());
      for (std::size_t k = 0; k < src.size(); ++k) {
        window_node.id[k] = src[k] < 128 ? static_cast<char>(src[k]) : '?';
      }
    }
    --remaining;
    WalkAx(win, 0, &window_node, pid, 0, &remaining);
    snap.controls.push_back(std::move(window_node));
  }
  CFRelease(wins);
  CFRelease(app);
  if (!snap.controls.empty()) {
    snap.mode = PerceptionMode::Uia;
    snap.window.pid = pid;
    snap.window.title = snap.controls.front().name;
    snap.detail = "AXUIElement snapshot ok (per-pid)";
  }
  return snap;
#else
  PerceptionSnapshot snap;
  snap.mode = PerceptionMode::Memory;
  snap.process.pid = pid;
  snap.detail = "no visible hwnd for pid — UIA miss, memory-primary";

  HANDLE proc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
  if (proc) {
    UniqueHandle ph(proc);
    wchar_t image[MAX_PATH]{};
    DWORD n = MAX_PATH;
    if (QueryFullProcessImageNameW(ph.get(), 0, image, &n)) {
      snap.process.image.assign(image, n);
    }
  }
  DWORD sid = 0;
  ProcessIdToSessionId(pid, &sid);
  snap.process.session_id = sid;

  std::vector<WindowInfo> all = ListWindows();
  std::vector<WindowInfo> wins;
  for (auto& w : all) {
    if (w.pid == pid) wins.push_back(std::move(w));
  }
  if (wins.empty()) return snap;

  std::size_t best = 0;
  std::int64_t best_area = 0;
  for (std::size_t i = 0; i < wins.size(); ++i) {
    const std::int64_t area =
        static_cast<std::int64_t>(wins[i].bounds.w) * wins[i].bounds.h;
    if (area > best_area) {
      best_area = area;
      best = i;
    }
  }
  snap.window = wins[best];

  ComInit com;
  IUIAutomation* uia = nullptr;
  HRESULT hr = CoCreateInstance(CLSID_CUIAutomation, nullptr, CLSCTX_INPROC_SERVER,
                                IID_IUIAutomation, reinterpret_cast<void**>(&uia));
  if (FAILED(hr) || !uia) {
    snap.detail = "CoCreateInstance(CUIAutomation) failed — vision/memory fallback";
    return snap;
  }

  std::size_t remaining = ProcessPerception::kMaxTreeNodes;
  for (const auto& w : wins) {
    if (remaining == 0) break;
    IUIAutomationElement* root = nullptr;
    hr = uia->ElementFromHandle(reinterpret_cast<HWND>(static_cast<uintptr_t>(w.hwnd)),
                                &root);
    if (FAILED(hr) || !root) continue;
    ControlNode window_node;
    if (!ElementOf(root, &window_node, pid, w.hwnd)) {
      root->Release();
      continue;
    }
    IUIAutomationTreeWalker* walker = nullptr;
    hr = uia->get_ControlViewWalker(&walker);
    if (SUCCEEDED(hr) && walker) {
      Walk(walker, root, 0, &window_node, pid, w.hwnd, &remaining);
      walker->Release();
    } else {
      IUIAutomationCondition* true_cond = nullptr;
      uia->CreateTrueCondition(&true_cond);
      IUIAutomationElementArray* arr = nullptr;
      root->FindAll(TreeScope_Subtree, true_cond, &arr);
      if (true_cond) true_cond->Release();
      FlattenFindAll(arr, &window_node, pid, w.hwnd, remaining);
      if (arr) arr->Release();
    }
    snap.controls.push_back(std::move(window_node));
    root->Release();
  }
  uia->Release();
  if (!snap.controls.empty()) {
    snap.mode = PerceptionMode::Uia;
    snap.detail = "UIA snapshot ok (per-pid ControlViewWalker)";
  }
  return snap;
#endif
}

PerceptionSnapshot ProcessPerception::SnapshotWindows() {
  return Snapshot();
}

}  // namespace secdogie::atlas
