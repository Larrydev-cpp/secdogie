#pragma once

// Read-only process handle wrapper.
//
// Atlas never grants write, VM-op, or thread-create access. The wrapper
// masks the request *before* OpenProcess, then refuses if the caller asked
// for write bits rather than silently narrowing PROCESS_ALL_ACCESS — the
// model must request read rights on purpose.
//
// Windows only. Compiles as a no-op stub on other platforms so the headers
// can be parsed in CI.

#include "privilege_error.h"

#include <cstdint>
#include <string>
#include <utility>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
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

  // Open pid with a read-only mask. Fails if `desired` includes write bits
  // (including PROCESS_ALL_ACCESS) or if OpenProcess itself fails.
  static Result<ReadOnlyProcessHandle> Open(std::uint32_t pid, DWORD desired);

  HANDLE get() const noexcept { return handle_; }
  DWORD granted() const noexcept { return granted_; }
  std::uint32_t pid() const noexcept { return pid_; }
  bool valid() const noexcept { return handle_ != nullptr; }

  // Query-only helpers. Never ReadProcessMemory of arbitrary ranges.
  Result<std::wstring> ImageName() const;
  Result<std::uint32_t> SessionId() const;

 private:
  ReadOnlyProcessHandle(HANDLE h, DWORD granted, std::uint32_t pid) noexcept
      : handle_(h), granted_(granted), pid_(pid) {}

  HANDLE handle_ = nullptr;
  DWORD granted_ = 0;
  std::uint32_t pid_ = 0;
};

}  // namespace secdogie::atlas
