#include "hybrid_tree.h"
#include "memory_inspector.h"
#include "privilege_manager.h"
#include "process_perception.h"
#include "readonly_handle.h"
#include "token_wall.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>

#if !defined(_WIN32)
#include <unistd.h>
#endif

using namespace secdogie::atlas;

static void PutNarrow(const std::wstring& w) {
  for (wchar_t c : w) {
    if (c >= 32 && c < 127) std::putchar(static_cast<char>(c));
    else std::putchar('?');
  }
}

static void Usage() {
  std::fputs(
      "atlas_inspect — Model Control Terminal\n"
      "read-only hybrid UIA + process-memory inspector\n"
      "\n"
      "  atlas_inspect --list\n"
      "  atlas_inspect --self [--json] [--max-mb 32]\n"
      "  atlas_inspect --pid <n> [--token] [--json]\n"
      "                 [--include-mapped] [--include-exec] [--allow-debug]\n"
      "\n"
      "Opens PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, walks committed\n"
      "PAGE_READONLY / PAGE_READWRITE (or Linux r-- / rw-) private pages,\n"
      "extracts strings / DIB / PE / JSON, fuses them with the UIA tree if\n"
      "one is available, then CLOSES the handle before printing.\n"
      "\n"
      "Token wall: TOKEN_QUERY only. SYSTEM / TrustedInstaller / PPL /\n"
      "higher-integrity targets are refused. SeDebug is opt-in and dropped\n"
      "in the destructor. No standing handle, no standing privilege.\n"
      "\n"
      "Will not: WriteProcessMemory, VirtualProtectEx, CreateRemoteThread,\n"
      "TrustedInstaller impersonation, anti-EDR, lsass/csrss/PPL.\n",
      stderr);
}

int main(int argc, char** argv) {
  std::uint32_t pid = 0;
  bool self = false;
  bool json = false;
  bool list = false;
  bool token_only = false;
  InspectConfig cfg;

  for (int i = 1; i < argc; ++i) {
    const char* a = argv[i];
    if (std::strcmp(a, "--pid") == 0 && i + 1 < argc) {
      pid = static_cast<std::uint32_t>(std::strtoul(argv[++i], nullptr, 10));
    } else if (std::strcmp(a, "--self") == 0) {
      self = true;
    } else if (std::strcmp(a, "--json") == 0) {
      json = true;
    } else if (std::strcmp(a, "--list") == 0) {
      list = true;
    } else if (std::strcmp(a, "--token") == 0) {
      token_only = true;
    } else if (std::strcmp(a, "--include-mapped") == 0) {
      cfg.include_mapped = true;
    } else if (std::strcmp(a, "--include-exec") == 0) {
      cfg.include_executable = true;
    } else if (std::strcmp(a, "--allow-debug") == 0) {
      cfg.allow_debug_privilege = true;
    } else if (std::strcmp(a, "--max-mb") == 0 && i + 1 < argc) {
      const unsigned mb = static_cast<unsigned>(std::strtoul(argv[++i], nullptr, 10));
      cfg.max_bytes = static_cast<std::size_t>(mb) * 1024ull * 1024ull;
    } else if (std::strcmp(a, "-h") == 0 || std::strcmp(a, "--help") == 0) {
      Usage();
      return 0;
    } else {
      Usage();
      return 2;
    }
  }

  PrivilegeManager pm;
  Result<TokenSnapshot> own = QueryOwnToken();

  if (list) {
    const std::vector<ListedProcess> procs = ProcessPerception::ListProcesses();
    if (json) {
      std::printf("{\"count\":%zu,\"processes\":[", procs.size());
      for (std::size_t i = 0; i < procs.size(); ++i) {
        if (i) std::putchar(',');
        std::printf("{\"pid\":%u,\"image\":\"", procs[i].pid);
        PutNarrow(procs[i].image);
        std::printf("\"}");
      }
      std::printf("]}\n");
      return 0;
    }
    std::printf("atlas_inspect --list  (%zu processes)\n", procs.size());
    std::size_t shown = 0;
    for (const auto& p : procs) {
      if (shown >= 64) break;
      std::printf("  %7u  ", p.pid);
      PutNarrow(p.image);
      std::putchar('\n');
      ++shown;
    }
    return 0;
  }

  if (self) {
#if defined(_WIN32)
    pid = GetCurrentProcessId();
#else
    pid = static_cast<std::uint32_t>(getpid());
#endif
  }
  if (pid == 0) {
    Usage();
    return 2;
  }

  if (token_only) {
    Result<TokenSnapshot> tok = QueryProcessToken(pid);
    if (!tok) {
      std::fprintf(stderr, "token query failed: %s — %s\n",
                   PrivilegeCodeName(tok.error().code), tok.error().detail.c_str());
      return 1;
    }
    const TokenSnapshot& t = tok.value();
    if (json) {
      std::printf("{\"pid\":%u,\"identity\":\"%s\",\"integrity\":\"%s\","
                  "\"system\":%s,\"trusted_installer\":%s,\"protected\":%s,"
                  "\"elevated\":%s,\"sid\":\"%s\"}\n",
                  t.pid, IdentityName(t.identity), IntegrityName(t.integrity),
                  t.system ? "true" : "false",
                  t.trusted_installer ? "true" : "false",
                  t.protected_process ? "true" : "false",
                  t.elevated ? "true" : "false", t.sid.c_str());
      return 0;
    }
    std::printf("atlas_inspect --token\n");
    std::printf("  pid           %u\n", t.pid);
    std::printf("  image         ");
    PutNarrow(t.image);
    std::putchar('\n');
    std::printf("  identity      %s\n", IdentityName(t.identity));
    std::printf("  integrity     %s\n", IntegrityName(t.integrity));
    std::printf("  system        %s\n", t.system ? "yes" : "no");
    std::printf("  TI            %s\n", t.trusted_installer ? "YES — inspect refused" : "no");
    std::printf("  PPL           %s\n", t.protected_process ? "YES — inspect refused" : "no");
    std::printf("  elevated      %s\n", t.elevated ? "yes" : "no");
    std::printf("  sid           %s\n", t.sid.c_str());
    std::printf("  session       %u\n", t.session_id);
    std::printf("  privileges    %zu\n", t.privileges.size());
    if (own) {
      const PrivilegeError wall = AllowInspect(own.value(), t);
      std::printf("  wall          %s — %s\n", PrivilegeCodeName(wall.code),
                  wall.detail.c_str());
    }
    std::printf("  handle        closed (RAII UniqueHandle)\n");
    return 0;
  }

  ProcessPerception perception;
  PerceptionSnapshot uia = perception.Snapshot();

  Result<InspectSnapshot> mem = InspectPid(pid, cfg);
  if (!mem) {
    std::fprintf(stderr, "inspect failed: %s — %s\n",
                 PrivilegeCodeName(mem.error().code), mem.error().detail.c_str());
    return 1;
  }
  const InspectSnapshot& s = mem.value();
  const std::vector<HybridNode> fused = FuseTree(uia.controls, s.strings);

  if (json) {
    std::printf("{\"pid\":%u,\"image\":\"", s.pid);
    PutNarrow(s.image);
    std::printf("\",\"identity\":\"%s\",\"integrity\":\"%s\",\"regions_seen\":%zu,"
                "\"regions_read\":%zu,\"bytes_read\":%zu,\"strings\":%zu,\"dibs\":%zu,"
                "\"pe\":%zu,\"json\":%zu,\"fused\":%zu,\"handle_closed\":%s,"
                "\"token_closed\":%s}\n",
                IdentityName(s.token.identity), IntegrityName(s.token.integrity),
                s.stats.regions_seen, s.stats.regions_read, s.stats.bytes_read,
                s.stats.strings_found, s.stats.dibs_found, s.stats.pes_found,
                s.stats.json_found, fused.size(),
                s.stats.handle_closed ? "true" : "false",
                s.stats.token_closed ? "true" : "false");
    return 0;
  }

  std::printf("atlas_inspect  (model control terminal)\n");
  std::printf("  operator      %s / %s\n",
              own ? IdentityName(own.value().identity) : "?",
              own ? IntegrityName(own.value().integrity) : "?");
  std::printf("  pid           %u\n", s.pid);
  std::printf("  image         ");
  PutNarrow(s.image);
  std::putchar('\n');
  std::printf("  identity      %s\n", IdentityName(s.token.identity));
  std::printf("  integrity     %s\n", IntegrityName(s.token.integrity));
  std::printf("  uia nodes     %zu (%s)\n", uia.controls.size(),
              uia.mode == PerceptionMode::Uia
                  ? "uia"
                  : (uia.mode == PerceptionMode::Memory ? "memory-primary" : "unavailable"));
  std::printf("  regions seen  %zu\n", s.stats.regions_seen);
  std::printf("  regions read  %zu\n", s.stats.regions_read);
  std::printf("  bytes read    %zu\n", s.stats.bytes_read);
  std::printf("  chunks fail   %zu\n", s.stats.chunks_failed);
  std::printf("  strings       %zu\n", s.stats.strings_found);
  std::printf("  dib headers   %zu\n", s.stats.dibs_found);
  std::printf("  pe images     %zu\n", s.stats.pes_found);
  std::printf("  json blobs    %zu\n", s.stats.json_found);
  std::printf("  hybrid nodes  %zu\n", fused.size());
  std::printf("  modules       %zu\n", s.modules.size());
  std::printf("  handle        %s\n",
              s.stats.handle_closed ? "closed (RAII)" : "STILL OPEN — bug");
  std::printf("  token         %s\n",
              s.stats.token_closed ? "closed (RAII)" : "STILL OPEN — bug");

  std::size_t shown = 0;
  for (const auto& hit : s.strings) {
    if (shown >= 24) break;
    std::printf("    [%s 0x%llx] ", hit.utf16 ? "u16" : "u8",
                static_cast<unsigned long long>(hit.address));
    std::size_t n = 0;
    for (wchar_t c : hit.text) {
      if (n++ > 80) break;
      if (c >= 32 && c < 127) std::putchar(static_cast<char>(c));
      else std::putchar('?');
    }
    std::putchar('\n');
    ++shown;
  }
  for (const auto& pe : s.pes) {
    std::printf("    [pe  0x%llx] e_lfanew=%u machine=0x%x %s\n",
                static_cast<unsigned long long>(pe.address), pe.e_lfanew, pe.machine,
                pe.pe32plus ? "PE32+" : "PE32");
  }
  shown = 0;
  for (const auto& j : s.json) {
    if (shown >= 8) break;
    std::printf("    [json 0x%llx] ", static_cast<unsigned long long>(j.address));
    std::size_t n = 0;
    for (char c : j.text) {
      if (n++ > 80) break;
      if (c >= 32 && c < 127) std::putchar(c);
      else std::putchar('?');
    }
    std::putchar('\n');
    ++shown;
  }
  return 0;
}
