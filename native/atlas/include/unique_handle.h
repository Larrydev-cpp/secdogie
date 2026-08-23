#pragma once

// RAII for OS process resources. No standing handles: every Inspect path
// constructs one of these on the stack and the destructor releases it before
// the snapshot is returned to the caller.
//
// Windows: CloseHandle on a real OpenProcess / token HANDLE.
// Linux: process_vm_readv does not need a fd; we still own a /proc/<pid>
// lifetime flag so valid() becomes false the moment close() runs.
//
// Privileges: ScopedPrivilege enables a named privilege for its lifetime
// then disables it and closes the token. Atlas never leaves SeDebug on
// after a scan (downgrade wall).

#include "privilege_error.h"

#include <cstdint>
#include <utility>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <unistd.h>
#endif

namespace secdogie::atlas {

class UniqueHandle {
 public:
  UniqueHandle() noexcept = default;

#if defined(_WIN32)
  explicit UniqueHandle(HANDLE h) noexcept : handle_(h) {}
  HANDLE get() const noexcept { return handle_; }
#else
  explicit UniqueHandle(std::uint32_t pid) noexcept : pid_(pid), open_(pid != 0) {}
  std::uint32_t pid() const noexcept { return pid_; }
#endif

  UniqueHandle(const UniqueHandle&) = delete;
  UniqueHandle& operator=(const UniqueHandle&) = delete;

  UniqueHandle(UniqueHandle&& other) noexcept { move_from(other); }
  UniqueHandle& operator=(UniqueHandle&& other) noexcept {
    if (this != &other) {
      close();
      move_from(other);
    }
    return *this;
  }

  ~UniqueHandle() { close(); }

  bool valid() const noexcept {
#if defined(_WIN32)
    return handle_ != nullptr && handle_ != INVALID_HANDLE_VALUE;
#else
    return open_ && pid_ != 0;
#endif
  }

  void close() noexcept {
#if defined(_WIN32)
    if (handle_ && handle_ != INVALID_HANDLE_VALUE) {
      CloseHandle(handle_);
    }
    handle_ = nullptr;
#else
    open_ = false;
    pid_ = 0;
#endif
  }

  // Relinquish ownership without closing. Used only when transferring into
  // ReadOnlyProcessHandle. After this, valid() is false.
#if defined(_WIN32)
  HANDLE release() noexcept {
    HANDLE h = handle_;
    handle_ = nullptr;
    return h;
  }
#else
  std::uint32_t release() noexcept {
    std::uint32_t p = pid_;
    pid_ = 0;
    open_ = false;
    return p;
  }
#endif

 private:
#if defined(_WIN32)
  HANDLE handle_ = nullptr;
  void move_from(UniqueHandle& other) noexcept {
    handle_ = other.handle_;
    other.handle_ = nullptr;
  }
#else
  std::uint32_t pid_ = 0;
  bool open_ = false;
  void move_from(UniqueHandle& other) noexcept {
    pid_ = other.pid_;
    open_ = other.open_;
    other.pid_ = 0;
    other.open_ = false;
  }
#endif
};

// Enable a named privilege for the lifetime of the object, then disable it
// and close the token. Default-constructed / null name is a no-op.
class ScopedPrivilege {
 public:
#if defined(_WIN32)
  explicit ScopedPrivilege(const wchar_t* name);
#else
  explicit ScopedPrivilege(const wchar_t* name) { (void)name; }
#endif
  ~ScopedPrivilege();
  ScopedPrivilege(const ScopedPrivilege&) = delete;
  ScopedPrivilege& operator=(const ScopedPrivilege&) = delete;
  bool enabled() const noexcept { return enabled_; }

 private:
  bool enabled_ = false;
#if defined(_WIN32)
  HANDLE token_ = nullptr;
  std::uint64_t luid_low_ = 0;
  std::int32_t luid_high_ = 0;
#endif
};

// Enable SeDebugPrivilege for the lifetime of the object, then disable it
// and close the token. Default-constructed / enable=false is a no-op.
// Atlas never leaves SeDebug on after a scan.
class ScopedDebugPrivilege {
 public:
  explicit ScopedDebugPrivilege(bool enable);
  ~ScopedDebugPrivilege() = default;
  ScopedDebugPrivilege(const ScopedDebugPrivilege&) = delete;
  ScopedDebugPrivilege& operator=(const ScopedDebugPrivilege&) = delete;
  bool enabled() const noexcept { return priv_.enabled(); }

 private:
  ScopedPrivilege priv_;
};

}  // namespace secdogie::atlas
