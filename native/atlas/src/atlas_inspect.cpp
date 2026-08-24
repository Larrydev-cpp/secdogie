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
#include <string>
#include <vector>

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

static std::string NarrowStr(const std::wstring& w) {
  std::string s;
  s.reserve(w.size());
  for (wchar_t c : w) {
    if (c >= 32 && c < 127) s.push_back(static_cast<char>(c));
    else s.push_back('?');
  }
  return s;
}

static void JsonEscape(const char* s, std::size_t n) {
  std::putchar('"');
  for (std::size_t i = 0; i < n; ++i) {
    const unsigned char c = static_cast<unsigned char>(s[i]);
    if (c == '"' || c == '\\') {
      std::putchar('\\');
      std::putchar(static_cast<char>(c));
    } else if (c == '\n') {
      std::fputs("\\n", stdout);
    } else if (c == '\r') {
      std::fputs("\\r", stdout);
    } else if (c == '\t') {
      std::fputs("\\t", stdout);
    } else if (c < 0x20) {
      std::printf("\\u%04x", c);
    } else {
      std::putchar(static_cast<char>(c));
    }
  }
  std::putchar('"');
}

static void JsonStr(const std::string& s) { JsonEscape(s.data(), s.size()); }
static void JsonW(const std::wstring& w) { JsonStr(NarrowStr(w)); }

static std::string RegionKind(const RemoteRegion& r) {
  if (r.pathname == "[heap]" || r.pathname == "[private]") return "heap";
  if (r.pathname.rfind("[stack", 0) == 0) return "stack";
  if (r.pathname == "[image]") return "image";
  if (r.pathname == "[mapped]") return "file";
  if (r.pathname.empty() || r.pathname.rfind("[anon", 0) == 0) return "anon";
  if (r.pathname.rfind("[v", 0) == 0) return "vdso";
  if (!r.pathname.empty() && r.pathname[0] == '/') return "file";
  if (r.pathname.find('\\') != std::string::npos) return "file";
  if (r.pathname.size() > 1 && r.pathname[1] == ':') return "file";
  return "other";
}

static const char* PlatformName() {
#if defined(_WIN32)
  return "windows";
#elif defined(__APPLE__)
  return "macos";
#else
  return "linux";
#endif
}

static void Usage() {
  std::fputs(
      "atlas_inspect — Model Control Terminal (Windows / Linux / macOS)\n"
      "read-only hybrid UI-tree + process-memory inspector\n"
      "\n"
      "  Windows : IUIAutomationTreeWalker of the target PID's hwnds;\n"
      "            miss → OpenProcess read-only + VirtualQueryEx +\n"
      "            ReadProcessMemory (SEH).\n"
      "  Linux   : /proc/<pid>/maps + process_vm_readv.\n"
      "  macOS   : AXUIElement of the target PID; miss → task_for_pid +\n"
      "            mach_vm_region + mach_vm_read_overwrite.\n"
      "  Never writes the target. TI / PPL / VM_WRITE / ALL_ACCESS refused.\n"
      "\n"
      "  atlas_inspect --list [--json]\n"
      "  atlas_inspect --self [--json] [--max-mb 32] [--find NAME]\n"
      "  atlas_inspect --pid <n> [--json] [--token] [--find NAME]\n"
      "                 [--include-mapped] [--include-exec] [--allow-debug]\n"
      "                 [--max-strings 400]\n",
      stderr);
}

static void DumpToken(const TokenSnapshot& t) {
  std::printf("\"token\":{\"pid\":%u,\"identity\":", t.pid);
  JsonStr(IdentityName(t.identity));
  std::printf(",\"integrity\":");
  JsonStr(IntegrityName(t.integrity));
  std::printf(",\"system\":%s,\"trusted_installer\":%s,\"protected\":%s,"
              "\"elevated\":%s,\"session\":%u,\"sid\":",
              t.system ? "true" : "false",
              t.trusted_installer ? "true" : "false",
              t.protected_process ? "true" : "false",
              t.elevated ? "true" : "false", t.session_id);
  JsonStr(t.sid);
  std::printf(",\"image\":");
  JsonW(t.image);
  std::printf(",\"privileges\":[");
  for (std::size_t i = 0; i < t.privileges.size(); ++i) {
    if (i) std::putchar(',');
    JsonW(t.privileges[i]);
  }
  std::printf("]}");
}

static void DumpHybrid(const std::vector<HybridNode>& nodes) {
  std::printf("\"hybrid\":[");
  bool first = true;
  for (const auto& n : nodes) {
    std::vector<const HybridNode*> flat;
    const auto walk = [&](auto& self, const HybridNode& h) -> void {
      flat.push_back(&h);
      for (const auto& c : h.children) self(self, c);
    };
    walk(walk, n);
    for (const HybridNode* h : flat) {
      if (!first) std::putchar(',');
      first = false;
      std::printf("{\"source\":");
      JsonStr(HybridSourceName(h->source));
      std::printf(",\"id\":");
      JsonStr(h->id);
      std::printf(",\"role\":");
      JsonStr(RoleName(h->role));
      std::printf(",\"name\":");
      JsonW(h->name);
      std::printf(",\"automation_id\":");
      JsonW(h->automation_id);
      std::printf(",\"pid\":%u,\"hwnd\":%llu,\"address\":%llu,\"enabled\":%s}",
                  h->pid, static_cast<unsigned long long>(h->hwnd),
                  static_cast<unsigned long long>(h->address),
                  h->enabled ? "true" : "false");
    }
  }
  std::printf("]");
}

static const char* ProtectName(const RemoteRegion& r) {
#if defined(_WIN32)
  const unsigned p = r.protect;
  const unsigned base = p & 0xFFu;
  const char* n = "OTHER";
  switch (base) {
    case 0x01: n = "PAGE_NOACCESS"; break;
    case 0x02: n = "PAGE_READONLY"; break;
    case 0x04: n = "PAGE_READWRITE"; break;
    case 0x08: n = "PAGE_WRITECOPY"; break;
    case 0x10: n = "PAGE_EXECUTE"; break;
    case 0x20: n = "PAGE_EXECUTE_READ"; break;
    case 0x40: n = "PAGE_EXECUTE_READWRITE"; break;
    case 0x80: n = "PAGE_EXECUTE_WRITECOPY"; break;
  }
  static thread_local char buf[96];
  std::snprintf(buf, sizeof(buf), "%s%s%s%s", n, (p & 0x100) ? "|PAGE_GUARD" : "",
                (p & 0x200) ? "|PAGE_NOCACHE" : "", (p & 0x400) ? "|PAGE_WRITECOMBINE" : "");
  return buf;
#else
  static thread_local char buf[8];
  buf[0] = r.readable ? 'r' : '-';
  buf[1] = r.writable ? 'w' : '-';
  buf[2] = r.execute ? 'x' : '-';
  buf[3] = 0;
  return buf;
#endif
}

static void DumpControlTree(const std::vector<ControlNode>& roots) {
  std::printf("\"tree\":[");
  bool first = true;
  const auto walk = [&](auto& self, const ControlNode& n, int depth) -> void {
    if (!first) std::putchar(',');
    first = false;
    std::printf("{\"depth\":%d,\"id\":", depth);
    JsonStr(n.id);
    std::printf(",\"role\":");
    JsonStr(RoleName(n.role));
    std::printf(",\"name\":");
    JsonW(n.name);
    std::printf(",\"automation_id\":");
    JsonW(n.automation_id);
    std::printf(",\"hwnd\":%llu,\"pid\":%u,\"enabled\":%s,"
                "\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d}",
                static_cast<unsigned long long>(n.hwnd), n.pid,
                n.enabled ? "true" : "false", n.bounds.x, n.bounds.y, n.bounds.w,
                n.bounds.h);
    for (const auto& c : n.children) self(self, c, depth + 1);
  };
  for (const auto& r : roots) walk(walk, r, 0);
  std::printf("]");
}

static void DumpWindowsOf(std::uint32_t pid) {
  std::printf("\"windows\":[");
  bool first = true;
  for (const auto& w : ProcessPerception::ListWindows()) {
    if (pid != 0 && w.pid != pid) continue;
    if (!first) std::putchar(',');
    first = false;
    std::printf("{\"hwnd\":%llu,\"pid\":%u,\"title\":",
                static_cast<unsigned long long>(w.hwnd), w.pid);
    JsonW(w.title);
    std::printf(",\"class\":");
    JsonW(w.class_name);
    std::printf(",\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d,\"visible\":%s}",
                w.bounds.x, w.bounds.y, w.bounds.w, w.bounds.h,
                w.visible ? "true" : "false");
  }
  std::printf("]");
}

static void DumpUia(const PerceptionSnapshot& uia) {
  std::printf("\"uia\":{\"mode\":");
  JsonStr(uia.mode == PerceptionMode::Uia
              ? "uia"
              : (uia.mode == PerceptionMode::Memory ? "memory" : "vision-fallback"));
  std::printf(",\"detail\":");
  JsonStr(uia.detail);
  std::printf(",\"pid\":%u,\"hwnd\":%llu,\"title\":", uia.process.pid,
              static_cast<unsigned long long>(uia.window.hwnd));
  JsonW(uia.window.title);
  std::printf(",\"class\":");
  JsonW(uia.window.class_name);
  std::printf(",\"nodes\":%zu,", uia.controls.size());
  DumpControlTree(uia.controls);
  std::putchar('}');
}

static void DumpFull(const InspectSnapshot& s, const std::vector<HybridNode>& fused,
                     const PerceptionSnapshot& uia, const PrivilegeError* wall,
                     const ControlNode* found) {
  std::printf("{\"ok\":true,\"platform\":");
  JsonStr(PlatformName());
  std::printf(",\"pid\":%u,\"image\":", s.pid);
  JsonW(s.image);
  std::printf(",\"cmdline\":");
  JsonW(s.cmdline);
  std::printf(",\"rss_kb\":%llu,\"session\":%u,\"detail\":",
              static_cast<unsigned long long>(s.rss_kb), s.session_id);
  JsonStr(s.detail);
  std::putchar(',');
  DumpToken(s.token);
  std::printf(",\"wall\":{\"code\":");
  JsonStr(wall ? PrivilegeCodeName(wall->code) : "ok");
  std::printf(",\"detail\":");
  JsonStr(wall ? wall->detail : s.detail);
  std::printf("},\"stats\":{\"regions_seen\":%zu,\"regions_read\":%zu,"
              "\"bytes_read\":%zu,\"chunks_failed\":%zu,\"strings\":%zu,"
              "\"dibs\":%zu,\"pe\":%zu,\"json\":%zu,\"mapped\":%zu,"
              "\"handle_closed\":%s,\"token_closed\":%s}",
              s.stats.regions_seen, s.stats.regions_read, s.stats.bytes_read,
              s.stats.chunks_failed, s.stats.strings_found, s.stats.dibs_found,
              s.stats.pes_found, s.stats.json_found, s.mapped.size(),
              s.stats.handle_closed ? "true" : "false",
              s.stats.token_closed ? "true" : "false");
  std::printf(",");
  DumpUia(uia);
  std::printf(",");
  DumpWindowsOf(s.pid);

  std::printf(",\"regions\":[");
  for (std::size_t i = 0; i < s.regions.size(); ++i) {
    const RemoteRegion& r = s.regions[i];
    if (i) std::putchar(',');
    std::printf("{\"start\":%llu,\"size\":%llu,\"protect\":%u,"
                "\"priv\":%s,\"readable\":%s,\"writable\":%s,\"execute\":%s,"
                "\"guard\":%s,\"noaccess\":%s,\"scanned\":%s,\"kind\":",
                static_cast<unsigned long long>(r.start),
                static_cast<unsigned long long>(r.size), r.protect,
                r.priv ? "true" : "false", r.readable ? "true" : "false",
                r.writable ? "true" : "false", r.execute ? "true" : "false",
                r.guard ? "true" : "false", r.noaccess ? "true" : "false",
                r.scanned ? "true" : "false");
    JsonStr(RegionKind(r));
    std::printf(",\"protect_name\":");
    JsonStr(ProtectName(r));
    std::printf(",\"pathname\":");
    JsonStr(r.pathname);
    std::putchar('}');
  }
  std::printf("],\"strings\":[");
  for (std::size_t i = 0; i < s.strings.size(); ++i) {
    if (i) std::putchar(',');
    std::printf("{\"address\":%llu,\"encoding\":",
                static_cast<unsigned long long>(s.strings[i].address));
    JsonStr(s.strings[i].utf16 ? "utf16le" : "utf8");
    std::printf(",\"text\":");
    JsonW(s.strings[i].text);
    std::putchar('}');
  }
  std::printf("],\"dibs\":[");
  for (std::size_t i = 0; i < s.dibs.size(); ++i) {
    if (i) std::putchar(',');
    std::printf("{\"address\":%llu,\"width\":%d,\"height\":%d,\"bit_count\":%u}",
                static_cast<unsigned long long>(s.dibs[i].address),
                s.dibs[i].width, s.dibs[i].height, s.dibs[i].bit_count);
  }
  std::printf("],\"pe\":[");
  for (std::size_t i = 0; i < s.pes.size(); ++i) {
    if (i) std::putchar(',');
    std::printf("{\"address\":%llu,\"e_lfanew\":%u,\"machine\":%u,\"pe32plus\":%s}",
                static_cast<unsigned long long>(s.pes[i].address),
                s.pes[i].e_lfanew, s.pes[i].machine,
                s.pes[i].pe32plus ? "true" : "false");
  }
  std::printf("],\"json\":[");
  for (std::size_t i = 0; i < s.json.size(); ++i) {
    if (i) std::putchar(',');
    std::printf("{\"address\":%llu,\"text\":",
                static_cast<unsigned long long>(s.json[i].address));
    JsonStr(s.json[i].text);
    std::putchar('}');
  }
  std::printf("],\"mapped\":[");
  for (std::size_t i = 0; i < s.mapped.size(); ++i) {
    if (i) std::putchar(',');
    std::printf("{\"start\":%llu,\"size\":%llu,\"path\":",
                static_cast<unsigned long long>(s.mapped[i].start),
                static_cast<unsigned long long>(s.mapped[i].size));
    JsonW(s.mapped[i].path);
    std::putchar('}');
  }
  std::printf("],");
  DumpHybrid(fused);
  std::printf(",\"found\":");
  if (found) {
    std::printf("{\"id\":");
    JsonStr(found->id);
    std::printf(",\"name\":");
    JsonW(found->name);
    std::printf(",\"automation_id\":");
    JsonW(found->automation_id);
    std::printf(",\"pid\":%u}", found->pid);
  } else {
    std::fputs("null", stdout);
  }
  std::printf("}\n");
}

int main(int argc, char** argv) {
  std::uint32_t pid = 0;
  bool self = false;
  bool json = false;
  bool list = false;
  bool token_only = false;
  InspectConfig cfg;
  std::wstring find_name;

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
    } else if (std::strcmp(a, "--max-strings") == 0 && i + 1 < argc) {
      cfg.max_strings = static_cast<std::size_t>(std::strtoul(argv[++i], nullptr, 10));
      if (cfg.max_strings == 0) cfg.max_strings = 64;
    } else if (std::strcmp(a, "--find") == 0 && i + 1 < argc) {
      const char* n = argv[++i];
      find_name.clear();
      for (const char* p = n; *p; ++p) {
        find_name.push_back(static_cast<wchar_t>(static_cast<unsigned char>(*p)));
      }
    } else if (std::strcmp(a, "-h") == 0 || std::strcmp(a, "--help") == 0) {
      Usage();
      return 0;
    } else {
      Usage();
      return 2;
    }
  }

  Result<TokenSnapshot> own = QueryOwnToken();

  if (list) {
    const std::vector<ListedProcess> procs = ProcessPerception::ListProcesses();
    if (json) {
      std::printf("{\"count\":%zu,\"platform\":", procs.size());
      JsonStr(PlatformName());
      std::printf(",\"operator\":");
      if (own) {
        std::printf("{\"identity\":");
        JsonStr(IdentityName(own.value().identity));
        std::printf(",\"integrity\":");
        JsonStr(IntegrityName(own.value().integrity));
        std::printf("}");
      } else {
        std::fputs("null", stdout);
      }
      std::printf(",\"processes\":[");
      for (std::size_t i = 0; i < procs.size(); ++i) {
        if (i) std::putchar(',');
        std::printf("{\"pid\":%u,\"rss_kb\":%llu,\"session\":%u,\"image\":", procs[i].pid,
                    static_cast<unsigned long long>(procs[i].rss_kb), procs[i].session_id);
        JsonW(procs[i].image);
        std::printf(",\"cmdline\":");
        std::wstring cmd = procs[i].cmdline;
        if (cmd.size() > 200) cmd.resize(200);
        JsonW(cmd);
        std::putchar('}');
      }
      std::printf("],\"windows\":[");
      const std::vector<WindowInfo> wins = ProcessPerception::ListWindows();
      for (std::size_t i = 0; i < wins.size(); ++i) {
        if (i) std::putchar(',');
        std::printf("{\"hwnd\":%llu,\"pid\":%u,\"title\":",
                    static_cast<unsigned long long>(wins[i].hwnd), wins[i].pid);
        JsonW(wins[i].title);
        std::printf(",\"class\":");
        JsonW(wins[i].class_name);
        std::putchar('}');
      }
      std::printf("]}\n");
      return 0;
    }
    std::printf("atlas_inspect --list  (%zu processes)\n", procs.size());
    std::size_t shown = 0;
    for (const auto& p : procs) {
      if (shown >= 128) break;
      std::printf("  %7u  %6llu kB  ", p.pid, static_cast<unsigned long long>(p.rss_kb));
      PutNarrow(p.image);
      if (!p.cmdline.empty()) {
        std::fputs("  ", stdout);
        PutNarrow(p.cmdline);
      }
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
    PrivilegeError wall{PrivilegeCode::Ok, "self inspect"};
    if (own) wall = AllowInspect(own.value(), t);
    if (json) {
      std::printf("{");
      DumpToken(t);
      std::printf(",\"wall\":{\"code\":");
      JsonStr(PrivilegeCodeName(wall.code));
      std::printf(",\"detail\":");
      JsonStr(wall.detail);
      std::printf("}}\n");
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
    std::printf("  TI            %s\n", t.trusted_installer ? "YES" : "no");
    std::printf("  PPL           %s\n", t.protected_process ? "YES" : "no");
    std::printf("  elevated      %s\n", t.elevated ? "yes" : "no");
    std::printf("  sid           %s\n", t.sid.c_str());
    std::printf("  wall          %s — %s\n", PrivilegeCodeName(wall.code), wall.detail.c_str());
    return 0;
  }

  ProcessPerception perception;
  PerceptionSnapshot uia = perception.SnapshotPid(pid);
  Result<InspectSnapshot> mem = InspectPid(pid, cfg);
  if (!mem) {
    if (json) {
      std::printf("{\"ok\":false,\"pid\":%u,\"code\":", pid);
      JsonStr(PrivilegeCodeName(mem.error().code));
      std::printf(",\"detail\":");
      JsonStr(mem.error().detail);
      std::printf("}\n");
    } else {
      std::fprintf(stderr, "inspect failed: %s — %s\n",
                   PrivilegeCodeName(mem.error().code), mem.error().detail.c_str());
    }
    return 1;
  }
  const InspectSnapshot& s = mem.value();
  const std::vector<HybridNode> fused = FuseTree(uia.controls, s.strings, s.strings.size());
  const std::vector<ControlNode> as_controls = HybridAsControls(fused);
  const ControlNode* found = nullptr;
  if (!find_name.empty()) {
    Selector sel;
    sel.name = find_name;
    found = ProcessPerception::Find(as_controls, sel);
  }
  PrivilegeError wall{PrivilegeCode::Ok, "inspect allowed"};
  if (own) wall = AllowInspect(own.value(), s.token);

  if (json) {
    DumpFull(s, fused, uia, &wall, found);
    return found || find_name.empty() ? 0 : 3;
  }

  std::printf("atlas_inspect  (model control terminal)\n");
  std::printf("  pid           %u\n", s.pid);
  std::printf("  image         ");
  PutNarrow(s.image);
  std::putchar('\n');
  std::printf("  cmdline       ");
  PutNarrow(s.cmdline);
  std::putchar('\n');
  std::printf("  rss           %llu kB\n", static_cast<unsigned long long>(s.rss_kb));
  std::printf("  identity      %s / %s\n", IdentityName(s.token.identity),
              IntegrityName(s.token.integrity));
  std::printf("  wall          %s\n", PrivilegeCodeName(wall.code));
  std::printf("  regions       seen %zu  read %zu  kept %zu\n", s.stats.regions_seen,
              s.stats.regions_read, s.regions.size());
  std::printf("  bytes         %zu  fail %zu\n", s.stats.bytes_read, s.stats.chunks_failed);
  std::printf("  strings       %zu\n", s.stats.strings_found);
  std::printf("  dib/pe/json   %zu / %zu / %zu\n", s.stats.dibs_found, s.stats.pes_found,
              s.stats.json_found);
  std::printf("  mapped        %zu\n", s.mapped.size());
  std::printf("  hybrid        %zu\n", fused.size());
  std::printf("  handle        %s\n",
              s.stats.handle_closed ? "closed (RAII)" : "STILL OPEN — bug");

  std::size_t shown = 0;
  std::printf("  vad\n");
  for (const auto& r : s.regions) {
    if (shown >= 24) break;
    std::printf("    %s %s%s%s  %8llu kB  0x%llx  %s\n",
                r.scanned ? "SCAN" : "skip",
                r.readable ? "r" : "-",
                r.writable ? "w" : "-",
                r.execute ? "x" : "-",
                static_cast<unsigned long long>(r.size / 1024),
                static_cast<unsigned long long>(r.start),
                r.pathname.c_str());
    ++shown;
  }
  shown = 0;
  std::printf("  strings\n");
  for (const auto& hit : s.strings) {
    if (shown >= 32) break;
    std::printf("    [%s 0x%llx] ", hit.utf16 ? "u16" : "u8",
                static_cast<unsigned long long>(hit.address));
    PutNarrow(hit.text);
    std::putchar('\n');
    ++shown;
  }
  if (found) {
    std::printf("  find          HIT  ");
    PutNarrow(found->name);
    std::printf("  id=%s\n", found->id.c_str());
  } else if (!find_name.empty()) {
    std::printf("  find          MISS\n");
    return 3;
  }
  return 0;
}
