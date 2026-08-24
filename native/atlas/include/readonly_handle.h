#pragma once

// Read-only process handle wrapper.
//
// Atlas never grants write, VM-op, or thread-create access. The wrapper
// masks the request *before* OpenProcess, then refuses if the caller asked
// for write bits rather than silently narrowing PROCESS_ALL_ACCESS — the
// model must request read rights on purpose.
//
// Windows: real OpenProcess HANDLE, CloseHandle in the destructor.
// Linux: pid-scoped session using process_vm_readv; destructor clears
// valid() so a leaked "standing handle" is observable in tests.

#include "privilege_error.h"

#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
using HANDLE = void*;
using DWORD = unsigned long;
#endif

namespace secdogie::atlas {

inline constexpr DWORD kProcessVmRead = 0x0010;
inline constexpr DWORD kProcessVmWrite = 0x0020;
inline constexpr DWORD kProcessVmOperation = 0x0008;
inline constexpr DWORD kProcessCreateThread = 0x0002;
inline constexpr DWORD kProcessQueryInformation = 0x0400;
inline constexpr DWORD kProcessQueryLimitedInformation = 0x1000;
inline constexpr DWORD kProcessSetInformation = 0x0200;
inline constexpr DWORD kProcessAllAccess = 0x001FFFFF;

inline constexpr DWORD kReadOnlyAccess =
    kProcessVmRead | kProcessQueryInformation | kProcessQueryLimitedInformation;

inline constexpr DWORD kWriteAccessBits = kProcessVmWrite | kProcessVmOperation |
                                          kProcessCreateThread |
                                          kProcessSetInformation;

struct AccessDecision {
  DWORD granted = 0;
  DWORD stripped = 0;
  PrivilegeCode code = PrivilegeCode::Ok;
  bool refused = false;
};

AccessDecision EnforceReadOnly(DWORD desired) noexcept;

class ReadOnlyProcessHandle {
 public:
  ReadOnlyProcessHandle() = default;
  ReadOnlyProcessHandle(const ReadOnlyProcessHandle&) = delete;
  ReadOnlyProcessHandle& operator=(const ReadOnlyProcessHandle&) = delete;
  ReadOnlyProcessHandle(ReadOnlyProcessHandle&& other) noexcept;
  ReadOnlyProcessHandle& operator=(ReadOnlyProcessHandle&& other) noexcept;
  ~ReadOnlyProcessHandle();

  static Result<ReadOnlyProcessHandle> Open(std::uint32_t pid, DWORD desired);

  HANDLE get() const noexcept { return handle_; }
  DWORD granted() const noexcept { return granted_; }
  std::uint32_t pid() const noexcept { return pid_; }
  bool valid() const noexcept;

  Result<std::wstring> ImageName() const;
  Result<std::uint32_t> SessionId() const;
  Result<std::wstring> CommandLine() const;
  Result<std::uint64_t> WorkingSetKb() const;
  Result<std::size_t> Read(std::uint64_t addr, void* dst, std::size_t n) const;
  Result<std::vector<std::wstring>> ModuleNames() const;

 private:
  ReadOnlyProcessHandle(HANDLE h, DWORD granted, std::uint32_t pid, bool open) noexcept
      : handle_(h), granted_(granted), pid_(pid), open_(open) {}

  HANDLE handle_ = nullptr;
  DWORD granted_ = 0;
  std::uint32_t pid_ = 0;
  bool open_ = false;
};

}  // namespace secdogie::atlas
