#include "unique_handle.h"

#if defined(_WIN32)
#include <windows.h>
#endif

namespace secdogie::atlas {

#if defined(_WIN32)
ScopedPrivilege::ScopedPrivilege(const wchar_t* name) {
  if (!name || !*name) return;
  HANDLE token = nullptr;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                        &token)) {
    return;
  }
  token_ = token;
  LUID luid{};
  if (!LookupPrivilegeValueW(nullptr, name, &luid)) {
    return;
  }
  luid_low_ = luid.LowPart;
  luid_high_ = luid.HighPart;
  TOKEN_PRIVILEGES tp{};
  tp.PrivilegeCount = 1;
  tp.Privileges[0].Luid = luid;
  tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
  if (!AdjustTokenPrivileges(token_, FALSE, &tp, 0, nullptr, nullptr)) {
    return;
  }
  enabled_ = GetLastError() == ERROR_SUCCESS;
}
#endif

ScopedPrivilege::~ScopedPrivilege() {
#if defined(_WIN32)
  if (enabled_ && token_) {
    LUID luid{};
    luid.LowPart = static_cast<DWORD>(luid_low_);
    luid.HighPart = luid_high_;
    TOKEN_PRIVILEGES tp{};
    tp.PrivilegeCount = 1;
    tp.Privileges[0].Luid = luid;
    tp.Privileges[0].Attributes = 0;  // disable — downgrade wall
    AdjustTokenPrivileges(token_, FALSE, &tp, 0, nullptr, nullptr);
  }
  if (token_) {
    CloseHandle(token_);
    token_ = nullptr;
  }
#endif
  enabled_ = false;
}

ScopedDebugPrivilege::ScopedDebugPrivilege(bool enable)
#if defined(_WIN32)
    : priv_(enable ? SE_DEBUG_NAME : nullptr) {
#else
    : priv_(nullptr) {
  (void)enable;
#endif
}

}  // namespace secdogie::atlas
