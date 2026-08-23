#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "readonly_handle.h"

#include <sstream>
#include <string>
#include <vector>

#if defined(_WIN32)
#include <psapi.h>
#include <tlhelp32.h>
#pragma comment(lib, "psapi.lib")
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
#else
  const std::string path = "/proc/" + std::to_string(pid_) + "/exe";
  char buf[4096];
  const ssize_t n = readlink(path.c_str(), buf, sizeof(buf) - 1);
  if (n <= 0) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "readlink /proc/pid/exe failed"};
  }
  buf[n] = 0;
  std::wstring out;
  out.reserve(static_cast<std::size_t>(n));
  for (ssize_t i = 0; i < n; ++i) {
    out.push_back(static_cast<wchar_t>(static_cast<unsigned char>(buf[i])));
  }
  return out;
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
  __try {
    ok = ReadProcessMemory(handle_, reinterpret_cast<LPCVOID>(static_cast<uintptr_t>(addr)),
                           dst, n, &got);
  } __except (EXCEPTION_EXECUTE_HANDLER) {
    ok = FALSE;
    got = 0;
  }
#else
  ok = ReadProcessMemory(handle_, reinterpret_cast<LPCVOID>(static_cast<uintptr_t>(addr)),
                         dst, n, &got);
#endif
  if (!ok) {
    return PrivilegeError{PrivilegeCode::Failed, "ReadProcessMemory failed"};
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
    std::wstring w;
    w.reserve(path.size());
    for (unsigned char c : path) w.push_back(static_cast<wchar_t>(c));
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
