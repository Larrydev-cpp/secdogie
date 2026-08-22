#pragma once

// Privilege manager — operator-gated, read-only by default.
//
// Honest boundaries (match secdogie SECURITY.md):
//   * SYSTEM launch is the documented CreateProcessAsUser path from an
//     already-admin token (PsExec -s). It is not a UAC bypass.
//   * NT SERVICE\TrustedInstaller impersonation is REFUSED. Token theft of
//     the servicing identity is a security-boundary bypass; Atlas will not
//     implement it.
//   * Anti-EDR, unhooking, and handle-hiding are REFUSED.
//   * Process handles are opened read-only (see ReadOnlyProcessHandle).
//
// The vision model never chooses the allowlist. Commands must be declared
// by the operator at launch (--allow-elevated-command).

#include "privilege_error.h"
#include "readonly_handle.h"

#include <cstdint>
#include <string>
#include <vector>

namespace secdogie::atlas {

enum class Integrity { Low, Medium, High, System, Unknown };

enum class Identity { User, Admin, System, TrustedInstaller };

struct LaunchDecision {
  bool ok = false;
  PrivilegeCode code = PrivilegeCode::Failed;
  std::string detail;
};

struct ElevateResult {
  PrivilegeCode outcome = PrivilegeCode::Failed;
  std::uint32_t pid = 0;
  std::string detail;
};

class PrivilegeManager {
 public:
  explicit PrivilegeManager(std::vector<std::wstring> allowlist = {});

  Integrity CurrentIntegrity() const;
  bool IsElevated() const;
  std::int32_t ActiveConsoleSession() const;  // -1 if none

  // Always returns RefusedIdentity. Exists so tests can assert the wall.
  PrivilegeError TryImpersonateTrustedInstaller() const;

  // Always returns a refusal. Exists so tests can assert the wall.
  PrivilegeError TryEdrEvasion() const;

  Result<ReadOnlyProcessHandle> OpenReadOnly(std::uint32_t pid, DWORD desired) const;

  LaunchDecision PlanSystemLaunch(const std::wstring& command) const;

  // CreateProcessAsUser as SYSTEM into the interactive session.
  // Requires already-admin + exact allowlist match. Windows only.
  ElevateResult RunAllowlistedAsSystem(const std::wstring& command, bool show = true);

  const std::vector<std::wstring>& allowlist() const { return allowlist_; }

  static std::wstring NormalizeCommand(const std::wstring& command);
  static bool IsAllowlisted(const std::wstring& command,
                            const std::vector<std::wstring>& allowlist);

 private:
  std::vector<std::wstring> allowlist_;
};

}  // namespace secdogie::atlas
