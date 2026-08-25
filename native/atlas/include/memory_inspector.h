#pragma once

// Hybrid UIA + deep process inspection.
//
// Primary path is UI Automation. When the tree is empty / COM failed / the
// control is owner-drawn, InspectPid() opens a *read-only* process handle,
// walks committed PAGE_READONLY / PAGE_READWRITE regions with VirtualQueryEx
// (Windows), /proc/<pid>/maps (Linux), or mach_vm_region (macOS), then
// ReadProcessMemory / process_vm_readv / mach_vm_read_overwrite copies them
// in 64 KiB chunks.
//
// Decode is per-OS, not a single scanner:
//   Windows : UTF-16LE first, then UTF-8. JSON on the wire is UTF-8.
//   Linux / macOS : UTF-8 first (real multi-byte, CJK kept). UTF-16LE only
//                   when the run is real wide text (NUL high bytes or CJK
//                   units), never ASCII-pair garbage that looks like CJK.
//
// Safe boundary:
//   * PAGE_GUARD / PAGE_NOACCESS / execute-only skipped
//   * SEH (__try/__except on MSVC) around ReadProcessMemory so a bad page
//     never becomes an unhandled exception in *this* process; the target is
//     not written, so it cannot be crashed by a write
//   * chunk failure skips that chunk, does not abort the process
//   * lsass / csrss / smss / PPL image names refused (denied-protected)
//   * Token wall: SYSTEM/TI/higher-integrity targets refused (denied-escalate)
//   * handle is closed before InspectPid returns — no standing handle
//
// This is perception, not mutation. There is no WriteProcessMemory, no
// VirtualProtectEx, no CreateRemoteThread, no DLL inject. Control stays on
// the UIA / SendInput path in HybridControlLoop.
//
// TrustedInstaller token theft and anti-EDR remain refused.

#include "privilege_error.h"
#include "readonly_handle.h"
#include "token_wall.h"

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace secdogie::atlas {

struct InspectConfig {
  std::size_t max_bytes = 32ull * 1024ull * 1024ull;
  std::size_t chunk_bytes = 64ull * 1024ull;
  std::size_t max_regions = 4096;
  std::size_t max_strings = 2048;
  std::size_t min_string = 4;
  std::size_t max_string = 256;
  bool include_executable = false;
  bool include_mapped = false;
  bool include_stack = false;
  bool allow_debug_privilege = false;
};

struct RemoteRegion {
  std::uint64_t start = 0;
  std::uint64_t size = 0;
  std::uint32_t protect = 0;
  bool committed = false;
  bool priv = false;
  bool readable = false;
  bool writable = false;
  bool execute = false;
  bool guard = false;
  bool noaccess = false;
  bool scanned = false;
  std::string pathname;
};

struct MemoryHit {
  std::uint64_t address = 0;
  std::wstring text;
  bool utf16 = true;
  std::uint32_t pid = 0;
};

struct DibHit {
  std::uint64_t address = 0;
  std::int32_t width = 0;
  std::int32_t height = 0;
  std::uint16_t bit_count = 0;
};

struct PeHit {
  std::uint64_t address = 0;
  std::uint32_t e_lfanew = 0;
  std::uint16_t machine = 0;
  bool pe32plus = false;
};

struct JsonHit {
  std::uint64_t address = 0;
  std::string text;
};

struct LoadedModule {
  std::uint64_t start = 0;
  std::uint64_t size = 0;
  std::wstring path;
};

struct InspectStats {
  std::size_t regions_seen = 0;
  std::size_t regions_read = 0;
  std::size_t bytes_read = 0;
  std::size_t chunks_failed = 0;
  std::size_t strings_found = 0;
  std::size_t dibs_found = 0;
  std::size_t pes_found = 0;
  std::size_t json_found = 0;
  bool handle_closed = false;
  bool token_closed = false;
};

struct InspectSnapshot {
  std::uint32_t pid = 0;
  std::wstring image;
  std::wstring cmdline;
  std::uint32_t session_id = 0;
  std::uint64_t rss_kb = 0;
  TokenSnapshot token;
  std::vector<RemoteRegion> regions;
  std::vector<MemoryHit> strings;
  std::vector<DibHit> dibs;
  std::vector<PeHit> pes;
  std::vector<JsonHit> json;
  std::vector<std::wstring> modules;
  std::vector<LoadedModule> mapped;
  InspectStats stats;
  std::string detail;
};

bool RegionSafeToRead(const RemoteRegion& r, const InspectConfig& cfg) noexcept;
bool ImageNameDenied(const std::wstring& image) noexcept;

void ExtractStrings(const std::uint8_t* data, std::size_t n, std::uint64_t base,
                    std::uint32_t pid, const InspectConfig& cfg,
                    std::vector<MemoryHit>& out);

void ExtractDibs(const std::uint8_t* data, std::size_t n, std::uint64_t base,
                 const InspectConfig& cfg, std::vector<DibHit>& out);

void ExtractPe(const std::uint8_t* data, std::size_t n, std::uint64_t base,
               std::vector<PeHit>& out);

void ExtractJson(const std::uint8_t* data, std::size_t n, std::uint64_t base,
                 const InspectConfig& cfg, std::vector<JsonHit>& out);

Result<InspectSnapshot> InspectPid(std::uint32_t pid, const InspectConfig& cfg);
Result<InspectSnapshot> InspectHandle(const ReadOnlyProcessHandle& handle,
                                      const InspectConfig& cfg);

}  // namespace secdogie::atlas
