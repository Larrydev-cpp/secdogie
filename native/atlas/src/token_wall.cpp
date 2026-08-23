#include "token_wall.h"

#include "memory_inspector.h"
#include "unique_handle.h"

#include <fstream>
#include <sstream>
#include <string>
#include <cstdio>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <sddl.h>
#pragma comment(lib, "advapi32.lib")
#endif

#if !defined(_WIN32)
#include <unistd.h>
#endif

namespace secdogie::atlas {
namespace {

#if defined(_WIN32)
std::wstring BasenameW(const std::wstring& path) {
  const std::size_t slash = path.find_last_of(L"\\/");
  if (slash == std::wstring::npos) return path;
  return path.substr(slash + 1);
}
#endif

#if defined(_WIN32)

// NT SERVICE\TrustedInstaller. Hard-coded well-known SID; we compare, we
// never DuplicateToken / ImpersonateLoggedOnUser on it.
constexpr wchar_t kTrustedInstallerSid[] =
    L"S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";

bool EqualStringSid(PSID sid, const wchar_t* want) {
  if (!sid || !want) return false;
  PSID parsed = nullptr;
  if (!ConvertStringSidToSidW(want, &parsed) || !parsed) return false;
  const BOOL eq = EqualSid(sid, parsed);
  LocalFree(parsed);
  return eq != FALSE;
}

std::string SidToString(PSID sid) {
  if (!sid) return {};
  LPWSTR str = nullptr;
  if (!ConvertSidToStringSidW(sid, &str) || !str) return {};
  std::string out;
  for (wchar_t* p = str; *p; ++p) {
    if (*p >= 32 && *p < 127) out.push_back(static_cast<char>(*p));
  }
  LocalFree(str);
  return out;
}

Integrity RidToIntegrity(DWORD rid) {
  if (rid >= SECURITY_MANDATORY_SYSTEM_RID) return Integrity::System;
  if (rid >= SECURITY_MANDATORY_HIGH_RID) return Integrity::High;
  if (rid >= SECURITY_MANDATORY_MEDIUM_RID) return Integrity::Medium;
  return Integrity::Low;
}

bool QueryProtectedProcess(HANDLE proc) {
  // ProcessProtectionLevelInfo. Missing on old kernels → treat as unprotected
  // (do not false-positive deny operator GUI processes).
  using Fn = BOOL(WINAPI*)(HANDLE, int, void*, DWORD);
  HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
  if (!k32) return false;
  auto fn = reinterpret_cast<Fn>(GetProcAddress(k32, "GetProcessInformation"));
  if (!fn) return false;
  struct Prot {
    DWORD level;
  } prot{};
  // ProcessProtectionLevelInfo = 7 on current SDK.
  if (!fn(proc, 7, &prot, sizeof(prot))) return false;
  constexpr DWORD kNone = 0xFFFFFFFE;
  return prot.level != kNone && prot.level != 0xFFFFFFFF;
}

TokenSnapshot FillFromToken(HANDLE token, HANDLE proc, std::uint32_t pid) {
  TokenSnapshot s;
  s.pid = pid;
  s.queried = true;

  DWORD need = 0;
  GetTokenInformation(token, TokenUser, nullptr, 0, &need);
  if (need) {
    std::vector<unsigned char> buf(need);
    if (GetTokenInformation(token, TokenUser, buf.data(), need, &need)) {
      auto* user = reinterpret_cast<TOKEN_USER*>(buf.data());
      s.sid = SidToString(user->User.Sid);
      if (IsWellKnownSid(user->User.Sid, WinLocalSystemSid)) {
        s.system = true;
        s.identity = Identity::System;
        s.integrity = Integrity::System;
      }
      if (EqualStringSid(user->User.Sid, kTrustedInstallerSid)) {
        s.trusted_installer = true;
        s.identity = Identity::TrustedInstaller;
        s.integrity = Integrity::System;
      }
    }
  }

  need = 0;
  GetTokenInformation(token, TokenIntegrityLevel, nullptr, 0, &need);
  if (need) {
    std::vector<unsigned char> buf(need);
    if (GetTokenInformation(token, TokenIntegrityLevel, buf.data(), need, &need)) {
      auto* label = reinterpret_cast<TOKEN_MANDATORY_LABEL*>(buf.data());
      const DWORD rid = *GetSidSubAuthority(
          label->Label.Sid,
          static_cast<DWORD>(*GetSidSubAuthorityCount(label->Label.Sid) - 1));
      s.integrity = RidToIntegrity(rid);
    }
  }

  TOKEN_ELEVATION elev{};
  DWORD elev_sz = sizeof(elev);
  if (GetTokenInformation(token, TokenElevation, &elev, sizeof(elev), &elev_sz)) {
    s.elevated = elev.TokenIsElevated != 0;
    if (s.elevated && s.identity == Identity::User) s.identity = Identity::Admin;
  }

  DWORD sid = 0;
  DWORD sid_sz = sizeof(sid);
  if (GetTokenInformation(token, TokenSessionId, &sid, sizeof(sid), &sid_sz)) {
    s.session_id = sid;
  }

  need = 0;
  GetTokenInformation(token, TokenPrivileges, nullptr, 0, &need);
  if (need) {
    std::vector<unsigned char> buf(need);
    if (GetTokenInformation(token, TokenPrivileges, buf.data(), need, &need)) {
      auto* tp = reinterpret_cast<TOKEN_PRIVILEGES*>(buf.data());
      const DWORD n = tp->PrivilegeCount > 64 ? 64 : tp->PrivilegeCount;
      for (DWORD i = 0; i < n; ++i) {
        wchar_t name[128];
        DWORD nlen = 128;
        if (LookupPrivilegeNameW(nullptr, &tp->Privileges[i].Luid, name, &nlen)) {
          s.privileges.emplace_back(name);
        }
      }
    }
  }

  if (proc) s.protected_process = QueryProtectedProcess(proc);
  s.detail = "token queried (TOKEN_QUERY only; closed)";
  return s;
}

#endif  // _WIN32

#if !defined(_WIN32)
TokenSnapshot FillFromProcStatus(std::uint32_t pid) {
  TokenSnapshot s;
  s.pid = pid;
  const std::string path = "/proc/" + std::to_string(pid) + "/status";
  std::ifstream in(path);
  if (!in) {
    s.detail = "cannot open /proc/pid/status";
    return s;
  }
  std::string line;
  unsigned euid = 0xFFFFFFFFu;
  while (std::getline(in, line)) {
    if (line.rfind("Name:", 0) == 0) {
      std::string name = line.substr(5);
      while (!name.empty() && (name.front() == ' ' || name.front() == '\t')) name.erase(name.begin());
      s.image.clear();
      for (unsigned char c : name) s.image.push_back(static_cast<wchar_t>(c));
    } else if (line.rfind("Uid:", 0) == 0) {
      unsigned r = 0, e = 0, sav = 0, f = 0;
      if (std::sscanf(line.c_str(), "Uid:\t%u\t%u\t%u\t%u", &r, &e, &sav, &f) >= 2) {
        euid = e;
      }
    }
  }
  s.queried = true;
  if (euid == 0) {
    s.system = true;
    s.elevated = true;
    s.identity = Identity::System;
    s.integrity = Integrity::System;
    s.sid = "uid:0";
  } else {
    s.identity = Identity::User;
    s.integrity = Integrity::Medium;
    s.sid = "uid:" + std::to_string(euid);
  }
  s.detail = "token queried (/proc/pid/status; no standing fd)";
  return s;
}
#endif

}  // namespace

Result<TokenSnapshot> QueryOwnToken() {
#if defined(_WIN32)
  HANDLE token = nullptr;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token) || !token) {
    return PrivilegeError{PrivilegeCode::AccessDenied, "OpenProcessToken(self) failed"};
  }
  UniqueHandle tok(token);
  TokenSnapshot s = FillFromToken(tok.get(), GetCurrentProcess(), GetCurrentProcessId());
  return s;
#else
  return FillFromProcStatus(static_cast<std::uint32_t>(getpid()));
#endif
}

Result<TokenSnapshot> QueryProcessToken(std::uint32_t pid) {
  if (pid == 0) {
    return PrivilegeError{PrivilegeCode::DeniedEmpty, "pid 0 is not inspectable."};
  }
#if defined(_WIN32)
  if (pid == GetCurrentProcessId()) return QueryOwnToken();
  HANDLE proc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
  if (!proc) {
    std::ostringstream oss;
    oss << "OpenProcess(QUERY_LIMITED, " << pid << ") failed, GetLastError="
        << GetLastError();
    return PrivilegeError{PrivilegeCode::AccessDenied, oss.str()};
  }
  UniqueHandle proc_h(proc);
  HANDLE token = nullptr;
  if (!OpenProcessToken(proc_h.get(), TOKEN_QUERY, &token) || !token) {
    return PrivilegeError{PrivilegeCode::AccessDenied,
                          "OpenProcessToken(TOKEN_QUERY) failed — no impersonation attempted"};
  }
  UniqueHandle tok(token);
  TokenSnapshot s = FillFromToken(tok.get(), proc_h.get(), pid);
  wchar_t image[MAX_PATH];
  DWORD n = MAX_PATH;
  if (QueryFullProcessImageNameW(proc_h.get(), 0, image, &n)) {
    s.image = BasenameW(std::wstring(image, n));
  }
  return s;
#else
  return FillFromProcStatus(pid);
#endif
}

PrivilegeError AllowInspect(const TokenSnapshot& self, const TokenSnapshot& target) {
  if (self.pid != 0 && target.pid != 0 && self.pid == target.pid) {
    return PrivilegeError{PrivilegeCode::Ok, "self inspect"};
  }
  if (target.trusted_installer) {
    return PrivilegeError{
        PrivilegeCode::DeniedProtected,
        "Refused: target token is NT SERVICE\\TrustedInstaller. Atlas never "
        "inspects or impersonates the servicing identity."};
  }
  if (target.protected_process) {
    return PrivilegeError{
        PrivilegeCode::DeniedProtected,
        "Refused: target is a protected process (PPL). No PPL bypass."};
  }
  if (ImageNameDenied(target.image)) {
    return PrivilegeError{
        PrivilegeCode::DeniedProtected,
        "Refused: this image is a protected / servicing process "
        "(lsass/csrss/PPL family)."};
  }
  if (target.identity == Identity::TrustedInstaller) {
    return PrivilegeError{PrivilegeCode::RefusedIdentity,
                          "TrustedInstaller identity is never an inspect target."};
  }
  if (target.system && !self.system) {
    return PrivilegeError{
        PrivilegeCode::DeniedEscalate,
        "Refused: target is SYSTEM and the operator is not. The wall does not "
        "elevate the inspector to match. Start already-SYSTEM, or pick a "
        "user-session GUI process."};
  }
  const int sr = IntegrityRank(self.integrity);
  const int tr = IntegrityRank(target.integrity);
  if (sr >= 0 && tr >= 0 && tr > sr) {
    return PrivilegeError{
        PrivilegeCode::DeniedEscalate,
        "Refused: target integrity is higher than the operator. Atlas will "
        "not steal a higher token to close the gap (downgrade wall)."};
  }
  return PrivilegeError{PrivilegeCode::Ok, "inspect allowed"};
}

}  // namespace secdogie::atlas
