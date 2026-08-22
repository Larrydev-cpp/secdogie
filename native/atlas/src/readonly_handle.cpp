#include "readonly_handle.h"

#include <sstream>

#if defined(_WIN32)
#include <tlhelp32.h>
#include <psapi.h>
#pragma comment(lib, "psapi.lib")
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
    : handle_(other.handle_), granted_(other.granted_), pid_(other.pid_) {
  other.handle_ = nullptr;
  other.granted_ = 0;
  other.pid_ = 0;
}

ReadOnlyProcessHandle& ReadOnlyProcessHandle::operator=(
    ReadOnlyProcessHandle&& other) noexcept {
  if (this == &other) return *this;
  this->~ReadOnlyProcessHandle();
  handle_ = other.handle_;
  granted_ = other.granted_;
  pid_ = other.pid_;
  other.handle_ = nullptr;
  other.granted_ = 0;
  other.pid_ = 0;
  return *this;
}

ReadOnlyProcessHandle::~ReadOnlyProcessHandle() {
#if defined(_WIN32)
  if (handle_ && handle_ != INVALID_HANDLE_VALUE) {
    CloseHandle(handle_);
  }
#endif
  handle_ = nullptr;
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

#if !defined(_WIN32)
  (void)pid;
  return PrivilegeError{PrivilegeCode::Unsupported,
                        "ReadOnlyProcessHandle::Open is Windows-only."};
#else
  HANDLE h = OpenProcess(gate.granted, FALSE, pid);
  if (!h) {
    const DWORD err = GetLastError();
    std::ostringstream oss;
    oss << "OpenProcess(" << pid << ", 0x" << std::hex << gate.granted
        << ") failed, GetLastError=" << std::dec << err;
    return PrivilegeError{PrivilegeCode::AccessDenied, oss.str()};
  }
  return ReadOnlyProcessHandle(h, gate.granted, pid);
#endif
}

Result<std::wstring> ReadOnlyProcessHandle::ImageName() const {
  if (!valid()) {
    return PrivilegeError{PrivilegeCode::Failed, "handle closed"};
  }
#if !defined(_WIN32)
  return PrivilegeError{PrivilegeCode::Unsupported, "Windows-only"};
#else
  wchar_t buf[MAX_PATH];
  DWORD n = MAX_PATH;
  if (QueryFullProcessImageNameW(handle_, 0, buf, &n)) {
    return std::wstring(buf, n);
  }
  // Fallback: EnumProcessModules needs VM_READ on some builds.
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
#endif
}

Result<std::uint32_t> ReadOnlyProcessHandle::SessionId() const {
  if (!valid()) {
    return PrivilegeError{PrivilegeCode::Failed, "handle closed"};
  }
#if !defined(_WIN32)
  return PrivilegeError{PrivilegeCode::Unsupported, "Windows-only"};
#else
  DWORD sid = 0;
  if (!ProcessIdToSessionId(pid_, &sid)) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "ProcessIdToSessionId failed"};
  }
  return static_cast<std::uint32_t>(sid);
#endif
}

}  // namespace secdogie::atlas
