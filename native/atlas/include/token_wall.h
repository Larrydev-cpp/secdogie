#pragma once

// Privilege wall: token verification, inspect allow/deny, RAII drop.
//
// Atlas may QUERY a target token (TOKEN_QUERY only). It never:
//   * TOKEN_DUPLICATE / TOKEN_IMPERSONATE / TOKEN_ASSIGN_PRIMARY
//   * impersonates NT SERVICE\TrustedInstaller
//   * inspects PPL / lsass / csrss / services
//   * inspects a higher-integrity identity than the operator holds
//     (DeniedEscalate). SYSTEM inspect requires the operator already be
//     SYSTEM. That is a downgrade wall, not a UAC bypass.
//
// SeDebugPrivilege is opt-in and lives in ScopedPrivilege / ScopedDebugPrivilege
// — enabled for the call, disabled in the destructor. No standing privileges.

#include "privilege_error.h"
#include "privilege_manager.h"

#include <cstdint>
#include <string>
#include <vector>

namespace secdogie::atlas {

struct TokenSnapshot {
  std::uint32_t pid = 0;
  Integrity integrity = Integrity::Unknown;
  Identity identity = Identity::User;
  bool elevated = false;
  bool system = false;
  bool trusted_installer = false;
  bool protected_process = false;
  std::uint32_t session_id = 0;
  std::wstring image;
  std::string sid;
  std::vector<std::wstring> privileges;
  bool queried = false;
  std::string detail;
};

inline int IntegrityRank(Integrity i) noexcept {
  switch (i) {
    case Integrity::Low: return 0;
    case Integrity::Medium: return 1;
    case Integrity::High: return 2;
    case Integrity::System: return 3;
    case Integrity::Unknown: return -1;
  }
  return -1;
}

inline const char* IntegrityName(Integrity i) noexcept {
  switch (i) {
    case Integrity::Low: return "low";
    case Integrity::Medium: return "medium";
    case Integrity::High: return "high";
    case Integrity::System: return "system";
    case Integrity::Unknown: return "unknown";
  }
  return "unknown";
}

inline const char* IdentityName(Identity i) noexcept {
  switch (i) {
    case Identity::User: return "user";
    case Identity::Admin: return "admin";
    case Identity::System: return "system";
    case Identity::TrustedInstaller: return "trusted-installer";
  }
  return "user";
}

// TOKEN_QUERY of pid. Handle (process + token) closed before return.
Result<TokenSnapshot> QueryProcessToken(std::uint32_t pid);

// TOKEN_QUERY of the calling process. No OpenProcess.
Result<TokenSnapshot> QueryOwnToken();

// Pure decision. Does not open handles.
PrivilegeError AllowInspect(const TokenSnapshot& self, const TokenSnapshot& target);

}  // namespace secdogie::atlas
