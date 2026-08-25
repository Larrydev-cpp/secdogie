#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "readonly_handle.h"

#include "utf.h"

#include <cstdio>
#include <fstream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

#if defined(_WIN32)
#include <psapi.h>
#include <tlhelp32.h>
#pragma comment(lib, "psapi.lib")
#elif defined(__APPLE__)
#include <cerrno>
#include <csignal>
#include <cstring>
#include <libproc.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <sys/sysctl.h>
#include <unistd.h>
#else
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <cerrno>
#include <csignal>
#include <cstring>
#include <fstream>
#include <sys/uio.h>
#include <unistd.h>
#endif

namespace secdogie::atlas {
namespace {

// Pure-C helper so MSVC __try never sees C++ objects that need unwind
// (C2712). Keep this function free of any class with a non-trivial
// destructor.
#if defined(_WIN32) && defined(_MSC_VER)
BOOL SafeReadProcessMemory(HANDLE h, LPCVOID addr, void* dst, SIZE_T n,
                           SIZE_T* got) {
  BOOL ok = FALSE;
  *got = 0;
  __try {
    ok = ReadProcessMemory(h, addr, dst, n, got);
  } __except (EXCEPTION_EXECUTE_HANDLER) {
    ok = FALSE;
    *got = 0;
  }
  return ok;
}
#endif

}  // namespace

AccessDecision EnforceReadOnly(DWORD desired) noexcept {
  AccessDecision d;
  if (desired == 0) {
    d.refused = true;
    d.code = PrivilegeCode::DeniedEmpty;
    return d;
  }
  if (desired == kProcessAllAccess || (desired & kWriteAccessBits) != 0) {
    d.refused = true;
    d.code = PrivilegeCode::DeniedWrite;
    d.stripped = desired & (kWriteAccessBits | kProcessAllAccess);
    return d;
  }
  d.granted = desired & kReadOnlyAccess;
  d.stripped = desired & ~kReadOnlyAccess;
  if (d.granted == 0) {
    d.refused = true;
    d.code = PrivilegeCode::DeniedEmpty;
    return d;
  }
  d.code = d.stripped ? PrivilegeCode::Stripped : PrivilegeCode::Ok;
  return d;
}

ReadOnlyProcessHandle::ReadOnlyProcessHandle(ReadOnlyProcessHandle&& other) noexcept
    : handle_(other.handle_),
      granted_(other.granted_),
      pid_(other.pid_),
      open_(other.open_) {
  other.handle_ = nullptr;
  other.granted_ = 0;
  other.pid_ = 0;
  other.open_ = false;
}

ReadOnlyProcessHandle& ReadOnlyProcessHandle::operator=(
    ReadOnlyProcessHandle&& other) noexcept {
  if (this == &other) return *this;
  this->~ReadOnlyProcessHandle();
  handle_ = other.handle_;
  granted_ = other.granted_;
  pid_ = other.pid_;
  open_ = other.open_;
  other.handle_ = nullptr;
  other.granted_ = 0;
  other.pid_ = 0;
  other.open_ = false;
  return *this;
}

ReadOnlyProcessHandle::~ReadOnlyProcessHandle() {
#if defined(_WIN32)
  if (handle_ && handle_ != INVALID_HANDLE_VALUE) {
    CloseHandle(handle_);
  }
#elif defined(__APPLE__)
  if (handle_) {
    const auto task = static_cast<mach_port_t>(reinterpret_cast<uintptr_t>(handle_));
    if (task != mach_task_self()) {
      mach_port_deallocate(mach_task_self(), task);
    }
  }
#endif
  handle_ = nullptr;
  pid_ = 0;
  granted_ = 0;
  open_ = false;
}

bool ReadOnlyProcessHandle::valid() const noexcept {
#if defined(_WIN32)
  return open_ && handle_ != nullptr && handle_ != INVALID_HANDLE_VALUE;
#else
  return open_ && pid_ != 0;
#endif
}

Result<ReadOnlyProcessHandle> ReadOnlyProcessHandle::Open(std::uint32_t pid,
                                                          DWORD desired) {
  const AccessDecision gate = EnforceReadOnly(desired);
  if (gate.refused) {
    return PrivilegeError{
        gate.code,
        gate.code == PrivilegeCode::DeniedWrite
            ? "Write access refused. Atlas never grants PROCESS_VM_WRITE, "
              "PROCESS_VM_OPERATION, PROCESS_CREATE_THREAD, or PROCESS_ALL_ACCESS."
            : "Handle request empty after read-only mask."};
  }
  if (pid == 0) {
    return PrivilegeError{PrivilegeCode::DeniedEmpty, "pid 0 is not inspectable."};
  }

#if defined(_WIN32)
  HANDLE h = OpenProcess(gate.granted, FALSE, pid);
  if (!h) {
    const DWORD err = GetLastError();
    std::ostringstream oss;
    oss << "OpenProcess(" << pid << ", 0x" << std::hex << gate.granted
        << ") failed, GetLastError=" << std::dec << err;
    return PrivilegeError{PrivilegeCode::AccessDenied, oss.str()};
  }
  return ReadOnlyProcessHandle(h, gate.granted, pid, true);
#elif defined(__APPLE__)
  mach_port_t task = MACH_PORT_NULL;
  if (pid == static_cast<std::uint32_t>(getpid())) {
    task = mach_task_self();
  } else {
    const kern_return_t kr = task_for_pid(mach_task_self(), static_cast<int>(pid), &task);
    if (kr != KERN_SUCCESS) {
      std::ostringstream oss;
      oss << "task_for_pid(" << pid << ") kr=" << kr
          << " (same-user / debugger entitlement; SIP blocks unrelated tasks)";
      return PrivilegeError{PrivilegeCode::AccessDenied, oss.str()};
    }
  }
  return ReadOnlyProcessHandle(reinterpret_cast<HANDLE>(static_cast<uintptr_t>(task)),
                               gate.granted, pid, true);
#else
  if (kill(static_cast<pid_t>(pid), 0) != 0 && errno != EPERM) {
    return PrivilegeError{PrivilegeCode::AccessDenied,
                          "no such process (kill(pid,0) failed)"};
  }
  return ReadOnlyProcessHandle(nullptr, gate.granted, pid, true);
#endif
}

Result<std::wstring> ReadOnlyProcessHandle::ImageName() const {
  if (!valid()) {
    return PrivilegeError{PrivilegeCode::Failed, "handle closed"};
  }
#if defined(_WIN32)
  wchar_t buf[MAX_PATH];
  DWORD n = MAX_PATH;
  if (QueryFullProcessImageNameW(handle_, 0, buf, &n)) {
    return std::wstring(buf, n);
  }
  HMODULE mod = nullptr;
  DWORD needed = 0;
  if ((granted_ & kProcessVmRead) &&
      EnumProcessModules(handle_, &mod, sizeof(mod), &needed) && mod) {
    wchar_t name[MAX_PATH];
    if (GetModuleBaseNameW(handle_, mod, name, MAX_PATH)) {
      return std::wstring(name);
    }
  }
  return PrivilegeError{PrivilegeCode::AccessDenied,
                        "QueryFullProcessImageNameW / EnumProcessModules failed"};
#elif defined(__APPLE__)
  char buf[PROC_PIDPATHINFO_MAXSIZE];
  const int n = proc_pidpath(static_cast<int>(pid_), buf, sizeof(buf));
  if (n <= 0) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "proc_pidpath failed"};
  }
  // POSIX paths are UTF-8. Never byte→wchar (that is the old garble path).
  return Utf8ToWide(std::string(buf, static_cast<std::size_t>(n)));
#else
  const std::string path = "/proc/" + std::to_string(pid_) + "/exe";
  char buf[4096];
  const ssize_t n = readlink(path.c_str(), buf, sizeof(buf) - 1);
  if (n <= 0) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "readlink /proc/pid/exe failed"};
  }
  buf[n] = 0;
  return Utf8ToWide(std::string(buf, static_cast<std::size_t>(n)));
#endif
}

Result<std::uint32_t> ReadOnlyProcessHandle::SessionId() const {
  if (!valid()) {
    return PrivilegeError{PrivilegeCode::Failed, "handle closed"};
  }
#if defined(_WIN32)
  DWORD sid = 0;
  if (!ProcessIdToSessionId(pid_, &sid)) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "ProcessIdToSessionId failed"};
  }
  return static_cast<std::uint32_t>(sid);
#else
  return static_cast<std::uint32_t>(0);
#endif
}

Result<std::wstring> ReadOnlyProcessHandle::CommandLine() const {
  if (!valid()) {
    return PrivilegeError{PrivilegeCode::Failed, "handle closed"};
  }
#if defined(_WIN32)
  using NtQip = LONG(WINAPI*)(HANDLE, ULONG, PVOID, ULONG, PULONG);
  HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
  if (!ntdll) {
    return PrivilegeError{PrivilegeCode::Failed, "ntdll missing"};
  }
  auto ntq = reinterpret_cast<NtQip>(GetProcAddress(ntdll, "NtQueryInformationProcess"));
  if (!ntq) {
    return PrivilegeError{PrivilegeCode::Failed, "NtQueryInformationProcess missing"};
  }
  ULONG need = 0;
  ntq(handle_, 60 /* ProcessCommandLineInformation */, nullptr, 0, &need);
  if (need == 0 || need > 65536) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "command line not available"};
  }
  std::vector<unsigned char> buf(need);
  const LONG st = ntq(handle_, 60, buf.data(), need, &need);
  if (st < 0) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "ProcessCommandLineInformation refused"};
  }
  struct UStr {
    unsigned short length;
    unsigned short maximum_length;
    wchar_t* buffer;
  };
  auto* us = reinterpret_cast<UStr*>(buf.data());
  const auto* begin = reinterpret_cast<unsigned char*>(buf.data());
  const wchar_t* src = nullptr;
  const std::size_t chars = us->length / sizeof(wchar_t);
  const auto* p = reinterpret_cast<unsigned char*>(us->buffer);
  if (us->buffer && us->length >= 2 && p >= begin && p + us->length <= begin + buf.size()) {
    src = us->buffer;
  } else if (us->length >= 2 && sizeof(UStr) + us->length <= buf.size()) {
    src = reinterpret_cast<const wchar_t*>(buf.data() + sizeof(UStr));
  }
  if (!src || chars == 0) {
    return PrivilegeError{PrivilegeCode::Failed, "empty command line"};
  }
  return std::wstring(src, chars);
#elif defined(__APPLE__)
  int mib[3] = {CTL_KERN, KERN_PROCARGS2, static_cast<int>(pid_)};
  std::size_t len = 0;
  if (sysctl(mib, 3, nullptr, &len, nullptr, 0) != 0 || len < sizeof(int) || len > 65536) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "KERN_PROCARGS2 size failed"};
  }
  std::vector<char> buf(len);
  if (sysctl(mib, 3, buf.data(), &len, nullptr, 0) != 0) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "KERN_PROCARGS2 refused"};
  }
  int argc = 0;
  std::memcpy(&argc, buf.data(), sizeof(argc));
  char* p = buf.data() + sizeof(int);
  char* end = buf.data() + static_cast<std::ptrdiff_t>(len);
  while (p < end && *p) ++p;
  while (p < end && *p == 0) ++p;
  std::string raw;
  for (int i = 0; i < argc && p < end; ++i) {
    if (!raw.empty()) raw.push_back(' ');
    raw.append(p);
    p += std::strlen(p) + 1;
  }
  // KERN_PROCARGS2 returns UTF-8 on modern macOS.
  return Utf8ToWide(raw);
#else
  const std::string path = "/proc/" + std::to_string(pid_) + "/cmdline";
  std::ifstream in(path, std::ios::binary);
  if (!in) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "cannot open /proc/pid/cmdline"};
  }
  std::string raw((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
  for (char& c : raw) {
    if (c == '\0') c = ' ';
  }
  while (!raw.empty() && raw.back() == ' ') raw.pop_back();
  // /proc/<pid>/cmdline is UTF-8.
  return Utf8ToWide(raw);
#endif
}

Result<std::uint64_t> ReadOnlyProcessHandle::WorkingSetKb() const {
  if (!valid()) {
    return PrivilegeError{PrivilegeCode::Failed, "handle closed"};
  }
#if defined(_WIN32)
  PROCESS_MEMORY_COUNTERS pmc{};
  if (!GetProcessMemoryInfo(handle_, &pmc, sizeof(pmc))) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "GetProcessMemoryInfo failed"};
  }
  return static_cast<std::uint64_t>(pmc.WorkingSetSize / 1024);
#elif defined(__APPLE__)
  proc_taskinfo ti{};
  const int n = proc_pidinfo(static_cast<int>(pid_), PROC_PIDTASKINFO, 0, &ti, sizeof(ti));
  if (n != static_cast<int>(sizeof(ti))) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "proc_pidinfo TASKINFO failed"};
  }
  return static_cast<std::uint64_t>(ti.pti_resident_size / 1024);
#else
  std::ifstream st("/proc/" + std::to_string(pid_) + "/status");
  std::string line;
  while (st && std::getline(st, line)) {
    if (line.rfind("VmRSS:", 0) == 0) {
      unsigned long kb = 0;
      if (std::sscanf(line.c_str() + 6, "%lu", &kb) == 1) {
        return static_cast<std::uint64_t>(kb);
      }
    }
  }
  return PrivilegeError{PrivilegeCode::Failed, "VmRSS missing"};
#endif
}

Result<std::size_t> ReadOnlyProcessHandle::Read(std::uint64_t addr, void* dst,
                                                std::size_t n) const {
  if (!valid()) {
    return PrivilegeError{PrivilegeCode::Failed, "handle closed"};
  }
  if (!dst || n == 0) {
    return PrivilegeError{PrivilegeCode::DeniedEmpty, "empty read"};
  }
#if defined(_WIN32)
  SIZE_T got = 0;
  BOOL ok = FALSE;
#if defined(_MSC_VER)
  ok = SafeReadProcessMemory(handle_,
                             reinterpret_cast<LPCVOID>(static_cast<uintptr_t>(addr)),
                             dst, n, &got);
#else
  ok = ReadProcessMemory(handle_, reinterpret_cast<LPCVOID>(static_cast<uintptr_t>(addr)),
                         dst, n, &got);
#endif
  if (!ok) {
    return PrivilegeError{PrivilegeCode::Failed, "ReadProcessMemory failed"};
  }
  return static_cast<std::size_t>(got);
#elif defined(__APPLE__)
  const auto task = static_cast<mach_port_t>(reinterpret_cast<uintptr_t>(handle_));
  mach_vm_size_t got = 0;
  const kern_return_t kr = mach_vm_read_overwrite(
      task, static_cast<mach_vm_address_t>(addr), n,
      reinterpret_cast<mach_vm_address_t>(dst), &got);
  if (kr != KERN_SUCCESS) {
    return PrivilegeError{PrivilegeCode::Failed, "mach_vm_read_overwrite failed"};
  }
  return static_cast<std::size_t>(got);
#else
  struct iovec local{};
  local.iov_base = dst;
  local.iov_len = n;
  struct iovec remote{};
  remote.iov_base = reinterpret_cast<void*>(static_cast<uintptr_t>(addr));
  remote.iov_len = n;
  const ssize_t r = process_vm_readv(static_cast<pid_t>(pid_), &local, 1, &remote, 1, 0);
  if (r < 0) {
    return PrivilegeError{PrivilegeCode::Failed,
                          std::string("process_vm_readv: ") + std::strerror(errno)};
  }
  return static_cast<std::size_t>(r);
#endif
}

Result<std::vector<std::wstring>> ReadOnlyProcessHandle::ModuleNames() const {
  if (!valid()) {
    return PrivilegeError{PrivilegeCode::Failed, "handle closed"};
  }
  std::vector<std::wstring> out;
#if defined(_WIN32)
  HMODULE mods[512];
  DWORD needed = 0;
  if (!EnumProcessModules(handle_, mods, sizeof(mods), &needed)) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "EnumProcessModules failed"};
  }
  const unsigned count = needed / sizeof(HMODULE);
  for (unsigned i = 0; i < count && i < 512; ++i) {
    wchar_t name[MAX_PATH];
    if (GetModuleBaseNameW(handle_, mods[i], name, MAX_PATH)) {
      out.emplace_back(name);
    }
  }
  return out;
#elif defined(__APPLE__)
  const auto task = static_cast<mach_port_t>(reinterpret_cast<uintptr_t>(handle_));
  mach_vm_address_t addr = 0;
  for (unsigned n = 0; n < 4096; ++n) {
    mach_vm_size_t size = 0;
    vm_region_basic_info_data_64_t info{};
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    const kern_return_t kr =
        mach_vm_region(task, &addr, &size, VM_REGION_BASIC_INFO_64,
                       reinterpret_cast<vm_region_info_t>(&info), &count, &object);
    if (kr != KERN_SUCCESS) break;
    if (object != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), object);
    char path[4096];
    const int got = proc_regionfilename(static_cast<int>(pid_), addr, path, sizeof(path));
    if (got > 0) {
      // proc_regionfilename returns UTF-8.
      std::wstring w = Utf8ToWide(std::string(path, static_cast<std::size_t>(got)));
      bool seen = false;
      for (const auto& e : out) {
        if (e == w) {
          seen = true;
          break;
        }
      }
      if (!seen) out.push_back(std::move(w));
    }
    if (addr + size <= addr) break;
    addr += size;
  }
  return out;
#else
  const std::string maps = "/proc/" + std::to_string(pid_) + "/maps";
  std::ifstream in(maps);
  if (!in) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "cannot open /proc/pid/maps"};
  }
  std::string line;
  while (std::getline(in, line)) {
    const auto slash = line.find('/');
    if (slash == std::string::npos) continue;
    const std::string path = line.substr(slash);
    // /proc maps paths are UTF-8.
    std::wstring w = Utf8ToWide(path);
    bool seen = false;
    for (const auto& e : out) {
      if (e == w) {
        seen = true;
        break;
      }
    }
    if (!seen) out.push_back(std::move(w));
  }
  return out;
#endif
}

}  // namespace secdogie::atlas
