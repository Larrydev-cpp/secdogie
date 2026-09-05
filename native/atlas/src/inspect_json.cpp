#include "inspect_json.h"

#include "hybrid_tree.h"
#include "privilege_manager.h"
#include "process_perception.h"
#include "token_wall.h"
#include "utf.h"

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace secdogie::atlas {
namespace {

struct JsonBuf {
  std::string s;
  void put(char c) { s.push_back(c); }
  void puts(const char* t) {
    if (t) s.append(t);
  }
  void append(const char* p, std::size_t n) { s.append(p, n); }
  void fmt(const char* f, ...) {
    char stack[512];
    va_list ap;
    va_start(ap, f);
    int n = std::vsnprintf(stack, sizeof(stack), f, ap);
    va_end(ap);
    if (n < 0) return;
    if (n < static_cast<int>(sizeof(stack))) {
      s.append(stack, static_cast<std::size_t>(n));
      return;
    }
    std::string heap(static_cast<std::size_t>(n) + 1, '\0');
    va_start(ap, f);
    std::vsnprintf(heap.data(), heap.size(), f, ap);
    va_end(ap);
    s.append(heap.data(), static_cast<std::size_t>(n));
  }
};

void JsonEscape(JsonBuf& o, const char* s, std::size_t n) {
  o.put('"');
  for (std::size_t i = 0; i < n; ++i) {
    const unsigned char c = static_cast<unsigned char>(s[i]);
    if (c == '"' || c == '\\') {
      o.put('\\');
      o.put(static_cast<char>(c));
    } else if (c == '\n') {
      o.puts("\\n");
    } else if (c == '\r') {
      o.puts("\\r");
    } else if (c == '\t') {
      o.puts("\\t");
    } else if (c < 0x20) {
      o.fmt("\\u%04x", c);
    } else {
      o.put(static_cast<char>(c));
    }
  }
  o.put('"');
}

void JsonStr(JsonBuf& o, const std::string& s) { JsonEscape(o, s.data(), s.size()); }
void JsonW(JsonBuf& o, const std::wstring& w) { JsonStr(o, WideToUtf8(w)); }

void JsonB64(JsonBuf& o, const std::uint8_t* p, std::size_t n) {
  static const char kTab[] =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  o.put('"');
  std::size_t i = 0;
  while (i + 3 <= n) {
    const unsigned v = (p[i] << 16) | (p[i + 1] << 8) | p[i + 2];
    o.put(kTab[(v >> 18) & 63]);
    o.put(kTab[(v >> 12) & 63]);
    o.put(kTab[(v >> 6) & 63]);
    o.put(kTab[v & 63]);
    i += 3;
  }
  if (i < n) {
    unsigned v = p[i] << 16;
    if (i + 1 < n) v |= p[i + 1] << 8;
    o.put(kTab[(v >> 18) & 63]);
    o.put(kTab[(v >> 12) & 63]);
    o.put(i + 1 < n ? kTab[(v >> 6) & 63] : '=');
    o.put('=');
  }
  o.put('"');
}

std::string RegionKind(const RemoteRegion& r) {
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

const char* ProtectName(const RemoteRegion& r) {
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

void DumpToken(JsonBuf& o, const TokenSnapshot& t) {
  o.fmt("\"token\":{\"pid\":%u,\"identity\":", t.pid);
  JsonStr(o, IdentityName(t.identity));
  o.puts(",\"integrity\":");
  JsonStr(o, IntegrityName(t.integrity));
  o.fmt(",\"system\":%s,\"trusted_installer\":%s,\"protected\":%s,"
        "\"elevated\":%s,\"session\":%u,\"sid\":",
        t.system ? "true" : "false", t.trusted_installer ? "true" : "false",
        t.protected_process ? "true" : "false", t.elevated ? "true" : "false",
        t.session_id);
  JsonStr(o, t.sid);
  o.puts(",\"image\":");
  JsonW(o, t.image);
  o.puts(",\"privileges\":[");
  for (std::size_t i = 0; i < t.privileges.size(); ++i) {
    if (i) o.put(',');
    JsonW(o, t.privileges[i]);
  }
  o.puts("]}");
}

void DumpControlTree(JsonBuf& o, const std::vector<ControlNode>& roots) {
  o.puts("\"tree\":[");
  bool first = true;
  const auto walk = [&](auto& self, const ControlNode& n, int depth) -> void {
    if (!first) o.put(',');
    first = false;
    o.fmt("{\"depth\":%d,\"id\":", depth);
    JsonStr(o, n.id);
    o.puts(",\"role\":");
    JsonStr(o, RoleName(n.role));
    o.puts(",\"name\":");
    JsonW(o, n.name);
    o.puts(",\"automation_id\":");
    JsonW(o, n.automation_id);
    o.fmt(",\"hwnd\":%llu,\"pid\":%u,\"enabled\":%s,\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d}",
          static_cast<unsigned long long>(n.hwnd), n.pid, n.enabled ? "true" : "false",
          n.bounds.x, n.bounds.y, n.bounds.w, n.bounds.h);
    for (const auto& c : n.children) self(self, c, depth + 1);
  };
  for (const auto& r : roots) walk(walk, r, 0);
  o.put(']');
}

void DumpWindowsOf(JsonBuf& o, std::uint32_t pid) {
  o.puts("\"windows\":[");
  bool first = true;
  for (const auto& w : ProcessPerception::ListWindows()) {
    if (pid != 0 && w.pid != pid) continue;
    if (!first) o.put(',');
    first = false;
    o.fmt("{\"hwnd\":%llu,\"pid\":%u,\"title\":", static_cast<unsigned long long>(w.hwnd),
          w.pid);
    JsonW(o, w.title);
    o.puts(",\"class\":");
    JsonW(o, w.class_name);
    o.fmt(",\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d,\"visible\":%s}", w.bounds.x, w.bounds.y,
          w.bounds.w, w.bounds.h, w.visible ? "true" : "false");
  }
  o.put(']');
}

void DumpUia(JsonBuf& o, const PerceptionSnapshot& uia) {
  o.puts("\"uia\":{\"mode\":");
  JsonStr(o, uia.mode == PerceptionMode::Uia
                 ? "uia"
                 : (uia.mode == PerceptionMode::Memory ? "memory" : "vision-fallback"));
  o.puts(",\"detail\":");
  JsonStr(o, uia.detail);
  o.fmt(",\"pid\":%u,\"hwnd\":%llu,\"title\":", uia.process.pid,
        static_cast<unsigned long long>(uia.window.hwnd));
  JsonW(o, uia.window.title);
  o.puts(",\"class\":");
  JsonW(o, uia.window.class_name);
  o.fmt(",\"nodes\":%zu,", uia.controls.size());
  DumpControlTree(o, uia.controls);
  o.put('}');
}

void DumpHybrid(JsonBuf& o, const std::vector<HybridNode>& nodes) {
  o.puts("\"hybrid\":[");
  bool first = true;
  for (const auto& n : nodes) {
    std::vector<const HybridNode*> flat;
    const auto walk = [&](auto& self, const HybridNode& h) -> void {
      flat.push_back(&h);
      for (const auto& c : h.children) self(self, c);
    };
    walk(walk, n);
    for (const HybridNode* h : flat) {
      if (!first) o.put(',');
      first = false;
      o.puts("{\"source\":");
      JsonStr(o, HybridSourceName(h->source));
      o.puts(",\"id\":");
      JsonStr(o, h->id);
      o.puts(",\"role\":");
      JsonStr(o, RoleName(h->role));
      o.puts(",\"name\":");
      JsonW(o, h->name);
      o.puts(",\"automation_id\":");
      JsonW(o, h->automation_id);
      o.fmt(",\"pid\":%u,\"hwnd\":%llu,\"address\":%llu,\"enabled\":%s}", h->pid,
            static_cast<unsigned long long>(h->hwnd),
            static_cast<unsigned long long>(h->address), h->enabled ? "true" : "false");
    }
  }
  o.put(']');
}

}  // namespace

const char* PlatformName() noexcept {
#if defined(_WIN32)
  return "windows";
#elif defined(__APPLE__)
  return "macos";
#else
  return "linux";
#endif
}

std::string DumpListJson() {
  JsonBuf o;
  const std::vector<ListedProcess> procs = ProcessPerception::ListProcesses();
  Result<TokenSnapshot> own = QueryOwnToken();
  o.fmt("{\"count\":%zu,\"platform\":", procs.size());
  JsonStr(o, PlatformName());
  o.puts(",\"operator\":");
  if (own) {
    o.puts("{\"identity\":");
    JsonStr(o, IdentityName(own.value().identity));
    o.puts(",\"integrity\":");
    JsonStr(o, IntegrityName(own.value().integrity));
    o.put('}');
  } else {
    o.puts("null");
  }
  o.puts(",\"processes\":[");
  for (std::size_t i = 0; i < procs.size(); ++i) {
    if (i) o.put(',');
    o.fmt("{\"pid\":%u,\"ppid\":%u,\"rss_kb\":%llu,\"session\":%u,\"readable\":%s,\"image\":",
          procs[i].pid, procs[i].ppid, static_cast<unsigned long long>(procs[i].rss_kb),
          procs[i].session_id, procs[i].readable ? "true" : "false");
    JsonW(o, procs[i].image);
    o.puts(",\"cmdline\":");
    std::wstring cmd = procs[i].cmdline;
    if (cmd.size() > 200) cmd.resize(200);
    JsonW(o, cmd);
    o.put('}');
  }
  o.puts("],\"windows\":[");
  const std::vector<WindowInfo> wins = ProcessPerception::ListWindows();
  for (std::size_t i = 0; i < wins.size(); ++i) {
    if (i) o.put(',');
    o.fmt("{\"hwnd\":%llu,\"pid\":%u,\"title\":", static_cast<unsigned long long>(wins[i].hwnd),
          wins[i].pid);
    JsonW(o, wins[i].title);
    o.puts(",\"class\":");
    JsonW(o, wins[i].class_name);
    o.put('}');
  }
  o.puts("]}");
  return o.s;
}

std::string DumpInspectJson(std::uint32_t pid, const InspectConfig& cfg,
                            const std::wstring& find_name) {
  JsonBuf o;
  Result<TokenSnapshot> own = QueryOwnToken();
  ProcessPerception perception;
  PerceptionSnapshot uia = perception.SnapshotPid(pid);
  Result<InspectSnapshot> mem = InspectPid(pid, cfg);

  InspectSnapshot empty;
  empty.pid = pid;
  empty.image = uia.process.image;
  empty.token.pid = pid;
  empty.token.image = uia.process.image;
  empty.detail = mem ? mem.value().detail : mem.error().detail;
  empty.stats.handle_closed = true;
  empty.stats.token_closed = true;
  const InspectSnapshot& s = mem ? mem.value() : empty;
  const std::vector<MemoryHit> no_hits;
  const std::vector<HybridNode> fused =
      FuseTree(uia.controls, mem ? s.strings : no_hits,
               mem ? s.strings.size() : 0);
  const std::vector<ControlNode> as_controls = HybridAsControls(fused);
  const ControlNode* found = nullptr;
  if (!find_name.empty()) {
    Selector sel;
    sel.name = find_name;
    found = ProcessPerception::Find(as_controls, sel);
    if (!found) found = ProcessPerception::Find(uia.controls, sel);
  }
  const bool ax_ok = !uia.controls.empty();
  const bool ok = static_cast<bool>(mem) || ax_ok;
  if (!ok) {
    o.fmt("{\"ok\":false,\"pid\":%u,\"code\":", pid);
    JsonStr(o, PrivilegeCodeName(mem.error().code));
    o.puts(",\"detail\":");
    JsonStr(o, mem.error().detail);
    o.puts(",\"uia\":");
    // still emit the (empty) tree + reason so the operator sees the OS grant, not a blank fail
    o.put('{');
    o.puts("\"mode\":");
    JsonStr(o, "memory");
    o.puts(",\"detail\":");
    JsonStr(o, uia.detail);
    o.puts("}}");
    return o.s;
  }

  PrivilegeError wall{PrivilegeCode::Ok, "inspect allowed"};
  if (own && mem) wall = AllowInspect(own.value(), s.token);
  else if (!mem) {
    wall = PrivilegeError{PrivilegeCode::Ok,
                          "UI tree only — process memory blocked (SIP / task_for_pid). "
                          "Not a product refusal."};
  }

  o.puts("{\"ok\":true,\"platform\":");
  JsonStr(o, PlatformName());
  o.puts(",\"decode\":{\"platform\":");
  JsonStr(o, PlatformName());
#if defined(_WIN32)
  o.puts(",\"primary\":\"utf-16le\",\"secondary\":\"utf-8\",\"json\":\"utf-8\"}");
#else
  o.puts(",\"primary\":\"utf-8\",\"secondary\":\"utf-16le\",\"json\":\"utf-8\"}");
#endif
  o.fmt(",\"pid\":%u,\"memory_ok\":%s,\"ax_ok\":%s,\"image\":", pid,
        mem ? "true" : "false", ax_ok ? "true" : "false");
  JsonW(o, s.image.empty() ? (s.token.image.empty() ? uia.process.image : s.token.image)
                           : s.image);
  o.puts(",\"cmdline\":");
  JsonW(o, s.cmdline);
  o.fmt(",\"rss_kb\":%llu,\"session\":%u,\"detail\":",
        static_cast<unsigned long long>(s.rss_kb), s.session_id);
  JsonStr(o, s.detail);
  o.put(',');
  DumpToken(o, s.token);
  o.puts(",\"wall\":{\"code\":");
  JsonStr(o, PrivilegeCodeName(wall.code));
  o.puts(",\"detail\":");
  JsonStr(o, wall.detail);
  o.fmt("},\"stats\":{\"regions_seen\":%zu,\"regions_read\":%zu,\"bytes_read\":%zu,"
        "\"chunks_failed\":%zu,\"strings\":%zu,\"dibs\":%zu,\"pe\":%zu,\"json\":%zu,"
        "\"mapped\":%zu,\"handle_closed\":%s,\"token_closed\":%s}",
        s.stats.regions_seen, s.stats.regions_read, s.stats.bytes_read, s.stats.chunks_failed,
        s.stats.strings_found, s.stats.dibs_found, s.stats.pes_found, s.stats.json_found,
        s.mapped.size(), s.stats.handle_closed ? "true" : "false",
        s.stats.token_closed ? "true" : "false");
  o.put(',');
  DumpUia(o, uia);
  o.put(',');
  DumpWindowsOf(o, pid);
  o.puts(",\"regions\":[");
  for (std::size_t i = 0; i < s.regions.size(); ++i) {
    const RemoteRegion& r = s.regions[i];
    if (i) o.put(',');
    o.fmt("{\"start\":%llu,\"size\":%llu,\"protect\":%u,\"priv\":%s,\"readable\":%s,"
          "\"writable\":%s,\"execute\":%s,\"guard\":%s,\"noaccess\":%s,\"scanned\":%s,\"kind\":",
          static_cast<unsigned long long>(r.start), static_cast<unsigned long long>(r.size),
          r.protect, r.priv ? "true" : "false", r.readable ? "true" : "false",
          r.writable ? "true" : "false", r.execute ? "true" : "false",
          r.guard ? "true" : "false", r.noaccess ? "true" : "false",
          r.scanned ? "true" : "false");
    JsonStr(o, RegionKind(r));
    o.puts(",\"protect_name\":");
    JsonStr(o, ProtectName(r));
    o.puts(",\"pathname\":");
    JsonStr(o, r.pathname);
    o.put('}');
  }
  o.puts("],\"strings\":[");
  for (std::size_t i = 0; i < s.strings.size(); ++i) {
    if (i) o.put(',');
    o.fmt("{\"address\":%llu,\"encoding\":", static_cast<unsigned long long>(s.strings[i].address));
    JsonStr(o, s.strings[i].utf16 ? "utf16le" : "utf8");
    o.puts(",\"text\":");
    JsonW(o, s.strings[i].text);
    o.put('}');
  }
  o.puts("],\"dibs\":[");
  for (std::size_t i = 0; i < s.dibs.size(); ++i) {
    if (i) o.put(',');
    o.fmt("{\"address\":%llu,\"width\":%d,\"height\":%d,\"bit_count\":%u,\"compression\":%u,"
          "\"preview\":",
          static_cast<unsigned long long>(s.dibs[i].address), s.dibs[i].width, s.dibs[i].height,
          s.dibs[i].bit_count, s.dibs[i].compression);
    if (s.dibs[i].rgba.empty()) o.puts("null");
    else JsonB64(o, s.dibs[i].rgba.data(), s.dibs[i].rgba.size());
    o.put('}');
  }
  o.puts("],\"pe\":[");
  for (std::size_t i = 0; i < s.pes.size(); ++i) {
    if (i) o.put(',');
    o.fmt("{\"address\":%llu,\"e_lfanew\":%u,\"machine\":%u,\"pe32plus\":%s}",
          static_cast<unsigned long long>(s.pes[i].address), s.pes[i].e_lfanew, s.pes[i].machine,
          s.pes[i].pe32plus ? "true" : "false");
  }
  o.puts("],\"json\":[");
  for (std::size_t i = 0; i < s.json.size(); ++i) {
    if (i) o.put(',');
    o.fmt("{\"address\":%llu,\"text\":", static_cast<unsigned long long>(s.json[i].address));
    JsonStr(o, s.json[i].text);
    o.put('}');
  }
  o.puts("],\"mapped\":[");
  for (std::size_t i = 0; i < s.mapped.size(); ++i) {
    if (i) o.put(',');
    o.fmt("{\"start\":%llu,\"size\":%llu,\"path\":",
          static_cast<unsigned long long>(s.mapped[i].start),
          static_cast<unsigned long long>(s.mapped[i].size));
    JsonW(o, s.mapped[i].path);
    o.put('}');
  }
  o.puts("],");
  DumpHybrid(o, fused);
  o.puts(",\"found\":");
  if (found) {
    o.puts("{\"id\":");
    JsonStr(o, found->id);
    o.puts(",\"name\":");
    JsonW(o, found->name);
    o.puts(",\"automation_id\":");
    JsonW(o, found->automation_id);
    o.fmt(",\"pid\":%u}", found->pid);
  } else {
    o.puts("null");
  }
  o.put('}');
  return o.s;
}

}  // namespace secdogie::atlas
