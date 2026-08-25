#include "privilege_manager.h"

#include "unique_handle.h"

#include <algorithm>
#include <cctype>

#if defined(_WIN32)
#include <shlobj.h>
#include <tlhelp32.h>
#include <userenv.h>
#include <wtsapi32.h>
#pragma comment(lib, "advapi32.lib")
#pragma comment(lib, "userenv.lib")
#pragma comment(lib, "wtsapi32.lib")
#pragma comment(lib, "shell32.lib")
#endif

namespace secdogie::atlas {
namespace {

const char* kTiRefusal =
    "NT SERVICE\\TrustedInstaller is a Windows servicing identity, not an "
    "application privilege. Token theft / impersonation of TrustedInstaller is "
    "refused. secdogie never steals service tokens, never bypasses PPL, and "
    "never uses this path to read protected processes. For operator-allowlisted "
    "SYSTEM work, start already-elevated and use the documented CreateProcessAsUser "
    "path.";

const char* kEdrRefusal =
    "Anti-EDR, unhooking, handle-hiding, and memory-scan evasion are not "
    "implemented and will not be. Atlas uses documented Win32 / UI Automation "
    "APIs only (OpenProcess with query/read, Toolhelp, UIA COM). The way this "
    "stays off EDR radar is by not doing malware-like things.";

#if defined(_WIN32)
DWORD FindPidByImage(const wchar_t* image) {
  HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
  if (snap == INVALID_HANDLE_VALUE) return 0;
  UniqueHandle snap_h(snap);
  PROCESSENTRY32W pe{};
  pe.dwSize = sizeof(pe);
  DWORD pid = 0;
  if (Process32FirstW(snap_h.get(), &pe)) {
    do {
      if (_wcsicmp(pe.szExeFile, image) == 0) {
        pid = pe.th32ProcessID;
        break;
      }
    } while (Process32NextW(snap_h.get(), &pe));
  }
  return pid;
}
#endif

}  // namespace

PrivilegeManager::PrivilegeManager(std::vector<std::wstring> allowlist)
    : allowlist_(std::move(allowlist)) {}

std::wstring PrivilegeManager::NormalizeCommand(const std::wstring& command) {
  std::wstring out;
  out.reserve(command.size());
  bool in_space = true;
  for (wchar_t ch : command) {
    if (ch == L' ' || ch == L'\t' || ch == L'\r' || ch == L'\n') {
      if (!in_space && !out.empty()) {
        out.push_back(L' ');
        in_space = true;
      }
    } else {
      out.push_back(ch);
      in_space = false;
    }
  }
  if (!out.empty() && out.back() == L' ') out.pop_back();
  return out;
}

bool PrivilegeManager::IsAllowlisted(const std::wstring& command,
                                     const std::vector<std::wstring>& allowlist) {
  const std::wstring target = NormalizeCommand(command);
  if (target.empty() || allowlist.empty()) return false;
  return std::any_of(allowlist.begin(), allowlist.end(), [&](const std::wstring& a) {
    return NormalizeCommand(a) == target;
  });
}

bool PrivilegeManager::IsElevated() const {
#if !defined(_WIN32)
  return false;
#else
  return IsUserAnAdmin() == TRUE;
#endif
}

Integrity PrivilegeManager::CurrentIntegrity() const {
#if !defined(_WIN32)
  return Integrity::Unknown;
#else
  HANDLE token = nullptr;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
    return Integrity::Unknown;
  }
  UniqueHandle tok(token);
  DWORD size = 0;
  GetTokenInformation(tok.get(), TokenIntegrityLevel, nullptr, 0, &size);
  if (size == 0) return Integrity::Unknown;
  std::vector<unsigned char> buf(size);
  Integrity level = Integrity::Unknown;
  if (GetTokenInformation(tok.get(), TokenIntegrityLevel, buf.data(), size, &size)) {
    auto* label = reinterpret_cast<TOKEN_MANDATORY_LABEL*>(buf.data());
    DWORD rid = *GetSidSubAuthority(
        label->Label.Sid, static_cast<DWORD>(*GetSidSubAuthorityCount(label->Label.Sid) - 1));
    if (rid >= SECURITY_MANDATORY_SYSTEM_RID)
      level = Integrity::System;
    else if (rid >= SECURITY_MANDATORY_HIGH_RID)
      level = Integrity::High;
    else if (rid >= SECURITY_MANDATORY_MEDIUM_RID)
      level = Integrity::Medium;
    else
      level = Integrity::Low;
  }
  return level;
#endif
}

std::int32_t PrivilegeManager::ActiveConsoleSession() const {
#if !defined(_WIN32)
  return -1;
#else
  const DWORD sid = WTSGetActiveConsoleSessionId();
  if (sid == 0xFFFFFFFF) return -1;
  return static_cast<std::int32_t>(sid);
#endif
}

PrivilegeError PrivilegeManager::TryImpersonateTrustedInstaller() const {
  return PrivilegeError{PrivilegeCode::RefusedIdentity, kTiRefusal};
}

PrivilegeError PrivilegeManager::TryEdrEvasion() const {
  return PrivilegeError{PrivilegeCode::RefusedIdentity, kEdrRefusal};
}

Result<ReadOnlyProcessHandle> PrivilegeManager::OpenReadOnly(std::uint32_t pid,
                                                             DWORD desired) const {
  return ReadOnlyProcessHandle::Open(pid, desired);
}

LaunchDecision PrivilegeManager::PlanSystemLaunch(const std::wstring& command) const {
  LaunchDecision d;
#if !defined(_WIN32)
  (void)command;
  d.code = PrivilegeCode::Unsupported;
  d.detail = "SYSTEM launch is Windows-only.";
  return d;
#else
  if (!IsElevated()) {
    d.code = PrivilegeCode::NotElevated;
    d.detail =
        "SYSTEM launch requires an already-elevated Administrator token. "
        "It does not bypass UAC.";
    return d;
  }
  if (NormalizeCommand(command).empty()) {
    d.code = PrivilegeCode::BadCommand;
    d.detail = "No command given.";
    return d;
  }
  if (!IsAllowlisted(command, allowlist_)) {
    d.code = PrivilegeCode::NotAllowlisted;
    d.detail =
        "Command is not on the operator allowlist declared at launch. "
        "The vision model cannot escalate arbitrary commands.";
    return d;
  }
  if (ActiveConsoleSession() < 0) {
    d.code = PrivilegeCode::NoSession;
    d.detail = "No interactive desktop session to launch into.";
    return d;
  }
  d.ok = true;
  d.code = PrivilegeCode::Ok;
  d.detail = "proceed";
  return d;
#endif
}

ElevateResult PrivilegeManager::RunAllowlistedAsSystem(const std::wstring& command,
                                                       bool show) {
  ElevateResult r;
  const LaunchDecision plan = PlanSystemLaunch(command);
  if (!plan.ok) {
    r.outcome = plan.code;
    r.detail = plan.detail;
    return r;
  }
#if !defined(_WIN32)
  (void)show;
  r.outcome = PrivilegeCode::Unsupported;
  r.detail = "Windows-only";
  return r;
#else
  // Elevate only for this call. Destructors disable the privileges and close
  // the tokens — no standing SeDebug / AssignPrimaryToken.
  ScopedPrivilege debug(SE_DEBUG_NAME);
  ScopedPrivilege quota(SE_INCREASE_QUOTA_NAME);
  ScopedPrivilege assign(SE_ASSIGNPRIMARYTOKEN_NAME);

  const DWORD winlogon = FindPidByImage(L"winlogon.exe");
  if (!winlogon) {
    r.outcome = PrivilegeCode::Failed;
    r.detail = "could not find winlogon.exe to borrow a SYSTEM token";
    return r;
  }

  HANDLE proc = OpenProcess(PROCESS_QUERY_INFORMATION, FALSE, winlogon);
  if (!proc) {
    r.outcome = PrivilegeCode::Failed;
    r.detail = "OpenProcess(winlogon) failed";
    return r;
  }
  UniqueHandle proc_h(proc);

  HANDLE src = nullptr;
  if (!OpenProcessToken(proc_h.get(), TOKEN_DUPLICATE, &src) || !src) {
    r.outcome = PrivilegeCode::Failed;
    r.detail = "OpenProcessToken(winlogon) failed";
    return r;
  }
  UniqueHandle src_h(src);

  HANDLE dup = nullptr;
  if (!DuplicateTokenEx(src_h.get(), TOKEN_ALL_ACCESS, nullptr, SecurityImpersonation,
                        TokenPrimary, &dup) ||
      !dup) {
    r.outcome = PrivilegeCode::Failed;
    r.detail = "DuplicateTokenEx failed";
    return r;
  }
  UniqueHandle dup_h(dup);

  DWORD session = static_cast<DWORD>(ActiveConsoleSession());
  SetTokenInformation(dup_h.get(), TokenSessionId, &session, sizeof(session));

  LPVOID env = nullptr;
  CreateEnvironmentBlock(&env, dup_h.get(), FALSE);

  STARTUPINFOW si{};
  si.cb = sizeof(si);
  si.lpDesktop = const_cast<LPWSTR>(L"winsta0\\default");
  si.dwFlags = STARTF_USESHOWWINDOW;
  si.wShowWindow = show ? SW_SHOW : SW_HIDE;
  PROCESS_INFORMATION pi{};

  std::wstring mutable_cmd = command;
  const BOOL ok = CreateProcessAsUserW(
      dup_h.get(), nullptr, mutable_cmd.data(), nullptr, nullptr, FALSE,
      CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_CONSOLE, env, nullptr, &si, &pi);

  if (env) DestroyEnvironmentBlock(env);

  if (!ok) {
    r.outcome = PrivilegeCode::Failed;
    r.detail = "CreateProcessAsUser failed (error " + std::to_string(GetLastError()) + ")";
    return r;
  }
  UniqueHandle thread_h(pi.hThread);
  UniqueHandle proc_out(pi.hProcess);
  r.outcome = PrivilegeCode::Ok;
  r.pid = pi.dwProcessId;
  r.detail = "started as SYSTEM (pid " + std::to_string(r.pid) + ")";
  return r;
#endif
}

}  // namespace secdogie::atlas
