#include "memory_inspector.h"

#include "token_wall.h"
#include "unique_handle.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

namespace secdogie::atlas {
namespace {

std::wstring Basename(const std::wstring& path) {
  const std::size_t slash = path.find_last_of(L"\\/");
  if (slash == std::wstring::npos) return path;
  return path.substr(slash + 1);
}

std::wstring ToLowerAscii(std::wstring s) {
  for (auto& c : s) {
    if (c >= L'A' && c <= L'Z') c = static_cast<wchar_t>(c + 32);
  }
  return s;
}

bool PrintableAscii(unsigned char c) { return c >= 0x20 && c < 0x7f; }

bool PrintableWide(wchar_t c) {
  if (c >= 0x20 && c < 0x7f) return true;
  if (c == 0x00d8 || c == 0x00f8) return true;
  if (c >= 0x00a0 && c <= 0x024f) return true;
  if (c >= 0x4e00 && c <= 0x9fff) return true;
  return false;
}

bool LooksLikeNoise(const std::wstring& s) {
  if (s.size() < 2) return true;
  bool all_same = true;
  for (std::size_t i = 1; i < s.size(); ++i) {
    if (s[i] != s[0]) {
      all_same = false;
      break;
    }
  }
  if (all_same) return true;
  unsigned letters = 0;
  for (wchar_t c : s) {
    if ((c >= L'A' && c <= L'Z') || (c >= L'a' && c <= L'z') ||
        (c >= 0x4e00 && c <= 0x9fff) || c == 0x00d8) {
      ++letters;
    }
  }
  if (letters < 3) return true;
  if (letters * 2 < s.size()) return true;
  return false;
}

bool JsonIdentChar(unsigned char c) {
  return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
         (c >= '0' && c <= '9') || c == '_' || c == '-';
}

#if defined(_WIN32)
RemoteRegion FromMbi(const MEMORY_BASIC_INFORMATION& m) {
  RemoteRegion r;
  r.start = reinterpret_cast<std::uint64_t>(m.BaseAddress);
  r.size = static_cast<std::uint64_t>(m.RegionSize);
  r.protect = m.Protect;
  r.committed = m.State == MEM_COMMIT;
  r.priv = m.Type == MEM_PRIVATE;
  const DWORD p = m.Protect & 0xFFu;
  r.noaccess = (p == PAGE_NOACCESS);
  r.guard = (m.Protect & PAGE_GUARD) != 0;
  r.execute = (p == PAGE_EXECUTE || p == PAGE_EXECUTE_READ ||
               p == PAGE_EXECUTE_READWRITE || p == PAGE_EXECUTE_WRITECOPY);
  r.readable = (p == PAGE_READONLY || p == PAGE_READWRITE || p == PAGE_WRITECOPY ||
                p == PAGE_EXECUTE_READ || p == PAGE_EXECUTE_READWRITE ||
                p == PAGE_EXECUTE_WRITECOPY);
  r.writable = (p == PAGE_READWRITE || p == PAGE_WRITECOPY ||
                p == PAGE_EXECUTE_READWRITE || p == PAGE_EXECUTE_WRITECOPY);
  return r;
}

std::vector<RemoteRegion> QueryWindows(HANDLE h) {
  std::vector<RemoteRegion> out;
  unsigned char* addr = nullptr;
  MEMORY_BASIC_INFORMATION mbi{};
  while (VirtualQueryEx(h, addr, &mbi, sizeof(mbi)) == sizeof(mbi)) {
    out.push_back(FromMbi(mbi));
    auto* next = reinterpret_cast<unsigned char*>(mbi.BaseAddress) + mbi.RegionSize;
    if (next <= addr) break;
    addr = next;
    if (out.size() > 65536) break;
  }
  return out;
}
#endif

#if !defined(_WIN32)
int ParseLinuxProtect(const std::string& perm) {
  int p = 0;
  if (perm.size() >= 1 && perm[0] == 'r') p |= 1;
  if (perm.size() >= 2 && perm[1] == 'w') p |= 2;
  if (perm.size() >= 3 && perm[2] == 'x') p |= 4;
  return p;
}

std::vector<RemoteRegion> QueryLinux(std::uint32_t pid) {
  std::vector<RemoteRegion> out;
  std::ifstream in("/proc/" + std::to_string(pid) + "/maps");
  if (!in) return out;
  std::string line;
  while (std::getline(in, line)) {
    unsigned long long start = 0, end = 0;
    char perm[8] = {};
    if (std::sscanf(line.c_str(), "%llx-%llx %7s", &start, &end, perm) < 3) continue;
    RemoteRegion r;
    r.start = start;
    r.size = end > start ? end - start : 0;
    const int p = ParseLinuxProtect(perm);
    r.protect = static_cast<std::uint32_t>(p);
    r.committed = r.size > 0;
    r.readable = (p & 1) != 0;
    r.writable = (p & 2) != 0;
    r.execute = (p & 4) != 0;
    r.noaccess = !r.readable && !r.writable && !r.execute;
    r.guard = false;
    const auto slash = line.find('/');
    const auto bracket = line.find('[');
    if (slash != std::string::npos) r.pathname = line.substr(slash);
    else if (bracket != std::string::npos) r.pathname = line.substr(bracket);
    r.priv = r.pathname.empty() || r.pathname == "[heap]" ||
             r.pathname.rfind("[anon", 0) == 0;
    out.push_back(r);
    if (out.size() > 65536) break;
  }
  return out;
}
#endif

std::vector<RemoteRegion> QueryRegions(const ReadOnlyProcessHandle& h) {
#if defined(_WIN32)
  return QueryWindows(h.get());
#else
  return QueryLinux(h.pid());
#endif
}

void DedupHits(std::vector<MemoryHit>& hits) {
  std::sort(hits.begin(), hits.end(), [](const MemoryHit& a, const MemoryHit& b) {
    if (a.text != b.text) return a.text < b.text;
    return a.address < b.address;
  });
  hits.erase(std::unique(hits.begin(), hits.end(),
                         [](const MemoryHit& a, const MemoryHit& b) {
                           return a.text == b.text;
                         }),
             hits.end());
}

int HitScore(const MemoryHit& h) {
  int letters = 0;
  int us = 0;
  int slash = 0;
  int space = 0;
  for (wchar_t c : h.text) {
    if ((c >= L'A' && c <= L'Z') || (c >= L'a' && c <= L'z') || (c >= 0x4e00 && c <= 0x9fff)) {
      ++letters;
    } else if (c == L'_') {
      ++us;
    } else if (c == L'/') {
      ++slash;
    } else if (c == L' ') {
      ++space;
    }
  }
  int s = letters + us * 12 + space * 6 + static_cast<int>(h.text.size() / 4);
  if (slash >= 2) s -= 50;
  return s;
}

void TrimHits(std::vector<MemoryHit>& hits, std::size_t maxn) {
  DedupHits(hits);
  if (hits.size() <= maxn) return;
  std::partial_sort(hits.begin(), hits.begin() + static_cast<std::ptrdiff_t>(maxn), hits.end(),
                    [](const MemoryHit& a, const MemoryHit& b) {
                      const int sa = HitScore(a);
                      const int sb = HitScore(b);
                      if (sa != sb) return sa > sb;
                      return a.text < b.text;
                    });
  hits.resize(maxn);
}

}  // namespace

bool RegionSafeToRead(const RemoteRegion& r, const InspectConfig& cfg) noexcept {
  if (!r.committed || r.size == 0) return false;
  if (r.noaccess || r.guard) return false;
  if (!r.readable) return false;
  if (r.execute && !cfg.include_executable) return false;
  if (!r.priv && !cfg.include_mapped) return false;
  if (!cfg.include_stack && r.pathname.find("[stack") != std::string::npos) return false;
  constexpr std::uint64_t kMaxRegion = 1ull << 30;
  if (r.size > kMaxRegion) return false;
  return true;
}

bool ImageNameDenied(const std::wstring& image) noexcept {
  const std::wstring base = ToLowerAscii(Basename(image));
  static const wchar_t* kDenied[] = {
      L"lsass.exe",
      L"csrss.exe",
      L"smss.exe",
      L"wininit.exe",
      L"services.exe",
      L"lsm.exe",
      L"winlogon.exe",
      L"memcompression",
      L"registry",
      L"msmpeng.exe",
      L"msmpengcp.exe",
      L"securityhealthservice.exe",
      L"secure system",
  };
  for (const wchar_t* d : kDenied) {
    if (base == d) return true;
  }
  return false;
}

void ExtractStrings(const std::uint8_t* data, std::size_t n, std::uint64_t base,
                    std::uint32_t pid, const InspectConfig& cfg,
                    std::vector<MemoryHit>& out) {
  if (!data || n == 0) return;
  const std::size_t min_n = cfg.min_string;
  const std::size_t max_n = cfg.max_string;

  for (std::size_t i = 0; i + 1 < n && out.size() < cfg.max_strings;) {
    wchar_t c = 0;
    std::memcpy(&c, data + i, 2);
    if (!PrintableWide(c)) {
      i += 2;
      continue;
    }
    std::size_t j = i;
    std::wstring s;
    s.reserve(16);
    while (j + 1 < n && s.size() < max_n) {
      wchar_t w = 0;
      std::memcpy(&w, data + j, 2);
      if (!PrintableWide(w)) break;
      s.push_back(w);
      j += 2;
    }
    if (s.size() >= min_n && !LooksLikeNoise(s)) {
      MemoryHit hit;
      hit.address = base + i;
      hit.text = std::move(s);
      hit.utf16 = true;
      hit.pid = pid;
      out.push_back(std::move(hit));
    }
    i = (j > i) ? j : i + 2;
  }

  for (std::size_t i = 0; i < n && out.size() < cfg.max_strings;) {
    if (!PrintableAscii(data[i])) {
      ++i;
      continue;
    }
    std::size_t j = i;
    while (j < n && j - i < max_n && PrintableAscii(data[j])) ++j;
    if (j - i >= min_n) {
      std::wstring s;
      s.reserve(j - i);
      for (std::size_t k = i; k < j; ++k) s.push_back(static_cast<wchar_t>(data[k]));
      if (!LooksLikeNoise(s)) {
        MemoryHit hit;
        hit.address = base + i;
        hit.text = std::move(s);
        hit.utf16 = false;
        hit.pid = pid;
        out.push_back(std::move(hit));
      }
    }
    i = j == i ? i + 1 : j;
  }
}

void ExtractDibs(const std::uint8_t* data, std::size_t n, std::uint64_t base,
                 const InspectConfig& cfg, std::vector<DibHit>& out) {
  (void)cfg;
  if (!data || n < 40) return;
  for (std::size_t i = 0; i + 40 <= n && out.size() < 64; i += 2) {
    std::uint32_t biSize = 0;
    std::int32_t biWidth = 0, biHeight = 0;
    std::uint16_t biPlanes = 0, biBitCount = 0;
    std::memcpy(&biSize, data + i, 4);
    if (biSize != 40 && biSize != 108 && biSize != 124) continue;
    std::memcpy(&biWidth, data + i + 4, 4);
    std::memcpy(&biHeight, data + i + 8, 4);
    std::memcpy(&biPlanes, data + i + 12, 2);
    std::memcpy(&biBitCount, data + i + 14, 2);
    const std::int32_t h = biHeight < 0 ? -biHeight : biHeight;
    if (biPlanes != 1) continue;
    if (biBitCount != 1 && biBitCount != 4 && biBitCount != 8 && biBitCount != 16 &&
        biBitCount != 24 && biBitCount != 32) {
      continue;
    }
    if (biWidth < 8 || biWidth > 8192 || h < 8 || h > 8192) continue;
    DibHit d;
    d.address = base + i;
    d.width = biWidth;
    d.height = h;
    d.bit_count = biBitCount;
    out.push_back(d);
  }
}

void ExtractPe(const std::uint8_t* data, std::size_t n, std::uint64_t base,
               std::vector<PeHit>& out) {
  if (!data || n < 64 || out.size() >= 32) return;
  if (data[0] != 'M' || data[1] != 'Z') return;
  std::uint32_t e_lfanew = 0;
  std::memcpy(&e_lfanew, data + 0x3c, 4);
  if (e_lfanew < 64 || static_cast<std::uint64_t>(e_lfanew) + 24 > n) return;
  if (data[e_lfanew] != 'P' || data[e_lfanew + 1] != 'E' || data[e_lfanew + 2] != 0 ||
      data[e_lfanew + 3] != 0) {
    return;
  }
  std::uint16_t machine = 0;
  std::memcpy(&machine, data + e_lfanew + 4, 2);
  std::uint16_t opt_magic = 0;
  if (static_cast<std::uint64_t>(e_lfanew) + 24 + 2 <= n) {
    std::memcpy(&opt_magic, data + e_lfanew + 24, 2);
  }
  PeHit pe;
  pe.address = base;
  pe.e_lfanew = e_lfanew;
  pe.machine = machine;
  pe.pe32plus = opt_magic == 0x20b;
  out.push_back(pe);
}

void ExtractJson(const std::uint8_t* data, std::size_t n, std::uint64_t base,
                 const InspectConfig& cfg, std::vector<JsonHit>& out) {
  if (!data || n < 8 || out.size() >= 64) return;
  const std::size_t cap = cfg.max_string < 512 ? 512 : cfg.max_string;
  for (std::size_t i = 0; i + 8 < n && out.size() < 64; ++i) {
    if (data[i] != '{') continue;
    // Require {"ident":
    if (i + 3 >= n || data[i + 1] != '"') continue;
    std::size_t k = i + 2;
    std::size_t ident = 0;
    while (k < n && JsonIdentChar(data[k]) && ident < 64) {
      ++k;
      ++ident;
    }
    if (ident < 2) continue;
    if (k + 1 >= n || data[k] != '"' || data[k + 1] != ':') continue;
    std::size_t j = i + 1;
    int depth = 1;
    while (j < n && j - i < cap && depth > 0) {
      const unsigned char c = data[j];
      if (c == '{') ++depth;
      else if (c == '}') --depth;
      else if (c == '"') {
        ++j;
        while (j < n && data[j] != '"') {
          if (data[j] == '\\' && j + 1 < n) ++j;
          ++j;
        }
      }
      ++j;
    }
    if (depth != 0) continue;
    const std::size_t len = j - i;
    if (len < 10) continue;
    JsonHit hit;
    hit.address = base + i;
    hit.text.assign(reinterpret_cast<const char*>(data + i), len);
    out.push_back(std::move(hit));
    i = j - 1;
  }
}

Result<InspectSnapshot> InspectHandle(const ReadOnlyProcessHandle& handle,
                                      const InspectConfig& cfg) {
  InspectSnapshot snap;
  snap.pid = handle.pid();
  if (!handle.valid()) {
    return PrivilegeError{PrivilegeCode::Failed, "handle closed before inspect"};
  }

  auto image = handle.ImageName();
  if (image) {
    snap.image = image.value();
    if (ImageNameDenied(snap.image)) {
      return PrivilegeError{
          PrivilegeCode::DeniedProtected,
          "Refused: this image is a protected / servicing process "
          "(lsass/csrss/PPL family). Atlas inspects operator GUI targets, "
          "not the OS security boundary."};
    }
  }
  auto sid = handle.SessionId();
  if (sid) snap.session_id = sid.value();
  auto mods = handle.ModuleNames();
  if (mods) snap.modules = mods.value();

#if !defined(_WIN32)
  {
    const std::string proc = "/proc/" + std::to_string(handle.pid());
    std::ifstream cmd(proc + "/cmdline", std::ios::binary);
    std::string raw((std::istreambuf_iterator<char>(cmd)),
                    std::istreambuf_iterator<char>());
    for (char& c : raw) {
      if (c == '\0') c = ' ';
    }
    while (!raw.empty() && raw.back() == ' ') raw.pop_back();
    snap.cmdline.clear();
    snap.cmdline.reserve(raw.size());
    for (unsigned char c : raw) snap.cmdline.push_back(static_cast<wchar_t>(c));
    std::ifstream st(proc + "/status");
    std::string line;
    while (st && std::getline(st, line)) {
      if (line.rfind("VmRSS:", 0) == 0) {
        unsigned long kb = 0;
        if (std::sscanf(line.c_str() + 6, "%lu", &kb) == 1) snap.rss_kb = kb;
        break;
      }
    }
  }
#endif

  const std::vector<RemoteRegion> regions = QueryRegions(handle);
  snap.stats.regions_seen = regions.size();
  snap.regions = regions;
  if (snap.regions.size() > cfg.max_regions) snap.regions.resize(cfg.max_regions);
  if (regions.empty()) {
    snap.detail =
        "VAD list empty: /proc/<pid>/maps unreadable (Yama ptrace_scope on "
        "unrelated PIDs) or the process has no mapped pages. Token/cmdline "
        "are still filled. Inspect a descendant (atlas_target) to exercise "
        "process_vm_readv.";
  }

  std::vector<std::uint8_t> chunk;
  chunk.resize(cfg.chunk_bytes == 0 ? 65536 : cfg.chunk_bytes);

  InspectConfig walk = cfg;
  walk.max_strings = std::max(cfg.max_strings * 8, std::size_t{8192});

  for (RemoteRegion& r : snap.regions) {
    if (snap.stats.regions_read >= cfg.max_regions) break;
    if (snap.stats.bytes_read >= cfg.max_bytes) break;
    if (!RegionSafeToRead(r, cfg)) continue;

    std::uint64_t off = 0;
    bool any = false;
    while (off < r.size) {
      if (snap.stats.bytes_read >= cfg.max_bytes) break;
      std::size_t want = chunk.size();
      if (off + want > r.size) want = static_cast<std::size_t>(r.size - off);
      auto got = handle.Read(r.start + off, chunk.data(), want);
      if (!got) {
        ++snap.stats.chunks_failed;
        off += want;
        continue;
      }
      const std::size_t n = got.value();
      if (n == 0) {
        ++snap.stats.chunks_failed;
        off += want;
        continue;
      }
      any = true;
      snap.stats.bytes_read += n;
      ExtractStrings(chunk.data(), n, r.start + off, handle.pid(), walk, snap.strings);
      ExtractDibs(chunk.data(), n, r.start + off, cfg, snap.dibs);
      if (off == 0) ExtractPe(chunk.data(), n, r.start, snap.pes);
      ExtractJson(chunk.data(), n, r.start + off, cfg, snap.json);
      if (n < want) break;
      off += n;
    }
    if (any) {
      r.scanned = true;
      ++snap.stats.regions_read;
    }
  }

  for (const RemoteRegion& r : snap.regions) {
    if (r.pathname.empty() || r.pathname[0] != '/') continue;
    bool seen = false;
    for (auto& m : snap.mapped) {
      std::string existing;
      existing.reserve(m.path.size());
      for (wchar_t c : m.path) {
        if (c >= 32 && c < 127) existing.push_back(static_cast<char>(c));
      }
      if (existing == r.pathname) {
        if (r.start < m.start) m.start = r.start;
        m.size += r.size;
        seen = true;
        break;
      }
    }
    if (seen) continue;
    LoadedModule m;
    m.start = r.start;
    m.size = r.size;
    m.path.reserve(r.pathname.size());
    for (unsigned char c : r.pathname) m.path.push_back(static_cast<wchar_t>(c));
    snap.mapped.push_back(std::move(m));
  }

  TrimHits(snap.strings, cfg.max_strings);
  snap.stats.strings_found = snap.strings.size();
  snap.stats.dibs_found = snap.dibs.size();
  snap.stats.pes_found = snap.pes.size();
  snap.stats.json_found = snap.json.size();
  if (snap.stats.regions_read > 0) {
    snap.detail = "read-only inspect ok";
  } else if (snap.detail.empty()) {
    snap.detail = "no safe private pages were readable";
  }
  return snap;
}

Result<InspectSnapshot> InspectPid(std::uint32_t pid, const InspectConfig& cfg) {
  if (pid == 0) {
    return PrivilegeError{PrivilegeCode::DeniedEmpty, "pid 0 is not inspectable."};
  }

  Result<TokenSnapshot> self_tok = QueryOwnToken();
  Result<TokenSnapshot> tgt_tok = QueryProcessToken(pid);
  if (self_tok && tgt_tok) {
    const PrivilegeError wall = AllowInspect(self_tok.value(), tgt_tok.value());
    if (wall.code != PrivilegeCode::Ok) return wall;
  } else if (tgt_tok && ImageNameDenied(tgt_tok.value().image)) {
    return PrivilegeError{PrivilegeCode::DeniedProtected,
                          "Refused: protected image (token query)."};
  }

  Result<ReadOnlyProcessHandle> opened =
      ReadOnlyProcessHandle::Open(pid, kReadOnlyAccess);
  if (!opened && cfg.allow_debug_privilege) {
    ScopedDebugPrivilege dbg(true);
    opened = ReadOnlyProcessHandle::Open(pid, kReadOnlyAccess);
  }
  if (!opened) return opened.error();

  Result<InspectSnapshot> snap = InspectHandle(opened.value(), cfg);
  if (snap) {
    snap.value().stats.handle_closed = true;
    snap.value().stats.token_closed = true;
    if (tgt_tok) snap.value().token = tgt_tok.take();
    else if (self_tok && pid == self_tok.value().pid) snap.value().token = self_tok.take();
  }
  return snap;
}

}  // namespace secdogie::atlas
