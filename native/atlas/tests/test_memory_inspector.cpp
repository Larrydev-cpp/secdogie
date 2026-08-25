// Real process-memory tests. A forked child (or this process) plants a heap
// marker; InspectPid must find it via process_vm_readv / ReadProcessMemory.
// No CAD fixture, no mock handle.

#include "hybrid_tree.h"
#include "hybrid_control_loop.h"
#include "memory_inspector.h"
#include "process_perception.h"
#include "readonly_handle.h"
#include "token_wall.h"
#include "utf.h"

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#if !defined(_WIN32)
#include <csignal>
#include <cstdlib>
#include <sys/wait.h>
#include <unistd.h>
#else
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

using namespace secdogie::atlas;

extern int g_failed;
extern int g_passed;
void Expect(bool cond, const char* name, const char* detail);

void RunMemoryInspectorTests() {
  {
    RemoteRegion bad;
    bad.committed = true;
    bad.size = 4096;
    bad.readable = true;
    bad.noaccess = true;
    bad.priv = true;
    Expect(!RegionSafeToRead(bad, InspectConfig{}), "PAGE_NOACCESS is not readable",
           "safe-boundary");
  }
  {
    RemoteRegion guard;
    guard.committed = true;
    guard.size = 4096;
    guard.readable = true;
    guard.writable = true;
    guard.guard = true;
    guard.priv = true;
    Expect(!RegionSafeToRead(guard, InspectConfig{}), "PAGE_GUARD is not readable",
           "safe-boundary");
  }
  {
    RemoteRegion exec;
    exec.committed = true;
    exec.size = 4096;
    exec.readable = true;
    exec.execute = true;
    exec.priv = true;
    Expect(!RegionSafeToRead(exec, InspectConfig{}), "execute page skipped by default",
           "safe-boundary");
    InspectConfig cfg;
    cfg.include_executable = true;
    Expect(RegionSafeToRead(exec, cfg), "execute page allowed when opted in",
           "safe-boundary");
  }
  {
    RemoteRegion rw;
    rw.committed = true;
    rw.size = 4096;
    rw.readable = true;
    rw.writable = true;
    rw.priv = true;
    Expect(RegionSafeToRead(rw, InspectConfig{}), "PAGE_READWRITE private is readable",
           "safe-boundary");
  }
  {
    Expect(ImageNameDenied(L"C:\\Windows\\System32\\lsass.exe"), "lsass.exe denied",
           "protected");
    Expect(ImageNameDenied(L"csrss.exe"), "csrss.exe denied", "protected");
    Expect(!ImageNameDenied(L"acad.exe"), "acad.exe allowed", "protected");
  }
  {
    const AccessDecision w = EnforceReadOnly(kProcessVmWrite);
    Expect(w.refused && w.code == PrivilegeCode::DeniedWrite,
           "inspector cannot obtain VM_WRITE", PrivilegeCodeName(w.code));
  }
  {
    unsigned char buf[64];
    std::memset(buf, 0, sizeof(buf));
    const char* m = "Zoom Extents";
    std::memcpy(buf, m, 12);
    InspectConfig cfg;
    cfg.min_string = 4;
    std::vector<MemoryHit> hits;
    ExtractStrings(buf, sizeof(buf), 0x1000, 1, cfg, hits);
    bool found = false;
    for (const auto& h : hits) {
      if (h.text == L"Zoom Extents") found = true;
    }
    Expect(found, "UTF-8 extractor finds Zoom Extents", found ? "ok" : "miss");
  }
  {
    unsigned char buf[64];
    std::memset(buf, 0, sizeof(buf));
    // Windows process memory stores wchar as UTF-16LE (2 bytes), not host wchar_t
    // (which is 4 bytes on Linux). Plant the real on-the-wire layout.
    const unsigned char u16[] = {'L', 0, 'A', 0, 'Y', 0, 'E', 0, 'R', 0,
                                 '_', 0, 'D', 0, 'I', 0, 'M', 0, 'S', 0, 0, 0};
    std::memcpy(buf, u16, sizeof(u16));
    InspectConfig cfg;
    cfg.min_string = 4;
    std::vector<MemoryHit> hits;
    ExtractStrings(buf, sizeof(buf), 0x2000, 1, cfg, hits);
    bool found = false;
    for (const auto& h : hits) {
      if (h.text == L"LAYER_DIMS") found = true;
    }
    Expect(found, "UTF-16LE extractor finds LAYER_DIMS", found ? "ok" : "miss");
  }
  {
    unsigned char buf[64];
    std::memset(buf, 0, sizeof(buf));
    std::memcpy(buf, "Zoom Extents", 12);
    InspectConfig cfg;
    cfg.min_string = 4;
    std::vector<MemoryHit> hits;
    ExtractStrings(buf, sizeof(buf), 0x1000, 1, cfg, hits);
    bool utf8_hit = false;
    bool utf16_hit = false;
    for (const auto& h : hits) {
      if (h.text == L"Zoom Extents" && !h.utf16) utf8_hit = true;
      if (h.utf16) utf16_hit = true;
    }
    Expect(utf8_hit, "UTF-8 ASCII stays UTF-8 plaintext", utf8_hit ? "ok" : "miss");
    Expect(!utf16_hit, "UTF-8 ASCII is not decoded as UTF-16LE CJK garbage",
           utf16_hit ? "garbage" : "clean");
  }
  {
    unsigned char buf[64];
    std::memset(buf, 0, sizeof(buf));
    const unsigned char cjk8[] = {0xE5, 0x9B, 0xBE, 0xE5, 0xB1, 0x82,
                                  0xE5, 0xB0, 0xBA, 0xE5, 0xAF, 0xB8};
    std::memcpy(buf, cjk8, sizeof(cjk8));
    InspectConfig cfg;
    cfg.min_string = 4;
    std::vector<MemoryHit> hits;
    ExtractStrings(buf, sizeof(buf), 0x3000, 1, cfg, hits);
    const std::wstring want = L"\u56fe\u5c42\u5c3a\u5bf8";
    bool found = false;
    for (const auto& h : hits) {
      if (h.text == want && !h.utf16) found = true;
    }
    Expect(found, "UTF-8 extractor keeps CJK plaintext", found ? "ok" : "miss");
    Expect(WideToUtf8(want) == std::string(reinterpret_cast<const char*>(cjk8), 12),
           "WideToUtf8 round-trips 图层尺寸", "utf8");
  }
  {
    unsigned char buf[64];
    std::memset(buf, 0, sizeof(buf));
    const unsigned char cjk16[] = {0xFE, 0x56, 0x42, 0x5C, 0x3A, 0x5C, 0xF8, 0x5B, 0, 0};
    std::memcpy(buf, cjk16, sizeof(cjk16));
    InspectConfig cfg;
    cfg.min_string = 4;
    std::vector<MemoryHit> hits;
    ExtractStrings(buf, sizeof(buf), 0x4000, 1, cfg, hits);
    const std::wstring want = L"\u56fe\u5c42\u5c3a\u5bf8";
    bool found = false;
    for (const auto& h : hits) {
      if (h.text == want && h.utf16) found = true;
    }
#if defined(_WIN32)
    Expect(found, "UTF-16LE extractor keeps CJK plaintext", found ? "ok" : "miss");
#else
    Expect(!found, "POSIX skips UTF-16LE CJK (UTF-8 is primary)",
           found ? "kept" : "skipped");
#endif
  }
  {
    ControlNode btn;
    btn.role = ControlRole::Button;
    btn.name = L"Zoom Extents";
    btn.automation_id = L"ID_ZOOM_EXTENTS";
    MemoryHit hit;
    hit.address = 0x7ff00000;
    hit.text = L"Zoom Extents";
    hit.pid = 9;
    const auto fused = FuseTree({btn}, {hit});
    Expect(!fused.empty() && fused[0].source == HybridSource::Fused &&
               fused[0].address == 0x7ff00000,
           "UIA + matching heap string becomes Fused",
           fused.empty() ? "empty" : HybridSourceName(fused[0].source));
  }
  {
    MemoryHit hit;
    hit.address = 0x1234;
    hit.text = L"owner-drawn layer";
    const auto fused = FuseTree({}, {hit});
    Expect(!fused.empty() && fused[0].source == HybridSource::Memory,
           "UIA-empty tree degrades to memory nodes",
           fused.empty() ? "empty" : HybridSourceName(fused[0].source));
  }

  // Live inspect of this process: plant a heap marker, Open, Read, Close.
  {
    char* marker = new char[64];
    std::memset(marker, 0, 64);
    std::memcpy(marker, "SECDOGIE_HYBRID_MARKER_v1", 25);
    marker[25] = 0;

#if defined(_WIN32)
    const std::uint32_t pid = GetCurrentProcessId();
#else
    const std::uint32_t pid = static_cast<std::uint32_t>(getpid());
#endif
    auto opened = ReadOnlyProcessHandle::Open(pid, kReadOnlyAccess);
    Expect(opened.ok() && opened.value().valid(), "Open(self) read-only succeeds",
           opened.ok() ? "ok" : PrivilegeCodeName(opened.error().code));

    InspectConfig cfg;
    cfg.max_bytes = 16ull * 1024ull * 1024ull;
    Result<InspectSnapshot> snap = InspectPid(pid, cfg);
    Expect(snap.ok(), "InspectPid(self) succeeds",
           snap.ok() ? snap.value().detail.c_str()
                     : PrivilegeCodeName(snap.error().code));
    if (snap) {
      Expect(snap.value().stats.handle_closed, "InspectPid closed the handle before return",
             snap.value().stats.handle_closed ? "closed" : "LEAK");
      bool found = false;
      for (const auto& h : snap.value().strings) {
        if (h.text.find(L"SECDOGIE_HYBRID_MARKER_v1") != std::wstring::npos) {
          found = true;
          break;
        }
      }
      Expect(found, "heap marker found via live memory inspect",
             found ? "hit" : "miss — Yama/ptrace_scope may block process_vm_readv");
      Expect(snap.value().stats.bytes_read > 0, "bytes actually copied from the target",
             "bytes_read");
      Expect(!snap.value().regions.empty(), "snapshot keeps the VAD region list",
             "regions");
      bool any_scanned = false;
      for (const auto& r : snap.value().regions) {
        if (r.scanned) any_scanned = true;
      }
      Expect(any_scanned, "at least one region marked scanned", "scanned");
    }

    // After InspectPid the caller's Open handle (if any) is independent;
    // moving it out and letting it die must flip valid() to false.
    if (opened) {
      ReadOnlyProcessHandle live = std::move(opened.value());
      Expect(live.valid(), "moved handle still valid", "raii");
      {
        ReadOnlyProcessHandle dead = std::move(live);
      }
      Expect(!live.valid(), "destructor releases handle (valid() == false)", "raii");
    }
    delete[] marker;
  }

#if !defined(_WIN32)
  {
    const pid_t child = fork();
    if (child == 0) {
      char* marker = static_cast<char*>(std::malloc(64));
      std::memcpy(marker, "SECDOGIE_CHILD_MARKER_v1", 24);
      marker[24] = 0;
      for (;;) pause();
    } else if (child > 0) {
      usleep(250000);
      InspectConfig cfg;
      cfg.max_bytes = 16ull * 1024ull * 1024ull;
      Result<InspectSnapshot> snap =
          InspectPid(static_cast<std::uint32_t>(child), cfg);
      kill(child, SIGKILL);
      waitpid(child, nullptr, 0);
      Expect(snap.ok(), "InspectPid(child) is a real foreign-process read",
             snap.ok() ? "ok" : PrivilegeCodeName(snap.error().code));
      if (snap) {
        bool found = false;
        for (const auto& h : snap.value().strings) {
          if (h.text.find(L"SECDOGIE_CHILD_MARKER_v1") != std::wstring::npos) {
            found = true;
            break;
          }
        }
        Expect(found, "child heap marker found (process_vm_readv)",
               found ? "hit" : "miss");
        Expect(snap.value().stats.handle_closed, "child inspect handle closed",
               "raii");
      }
    }
  }
#endif

#if defined(_WIN32)
  {
    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    wchar_t cmd[] = L"cmd.exe /c ping -n 4 127.0.0.1 >NUL";
    if (CreateProcessW(nullptr, cmd, nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr,
                       nullptr, &si, &pi)) {
      Sleep(250);
      InspectConfig cfg;
      cfg.max_bytes = 16ull * 1024ull * 1024ull;
      Result<InspectSnapshot> snap = InspectPid(pi.dwProcessId, cfg);
      TerminateProcess(pi.hProcess, 0);
      WaitForSingleObject(pi.hProcess, 2000);
      CloseHandle(pi.hThread);
      CloseHandle(pi.hProcess);
      Expect(snap.ok(), "InspectPid(cmd) is a real foreign-process RPM",
             snap.ok() ? "ok" : PrivilegeCodeName(snap.error().code));
      if (snap) {
        Expect(!snap.value().regions.empty(), "VirtualQueryEx returned VAD regions",
               "regions");
        Expect(snap.value().stats.handle_closed, "Windows foreign inspect handle closed",
               "raii");
      }
    }
  }
#endif

  {
    unsigned char pe[128];
    std::memset(pe, 0, sizeof(pe));
    pe[0] = 'M';
    pe[1] = 'Z';
    const std::uint32_t e_lfanew = 64;
    std::memcpy(pe + 0x3c, &e_lfanew, 4);
    pe[64] = 'P';
    pe[65] = 'E';
    pe[66] = 0;
    pe[67] = 0;
    const std::uint16_t machine = 0x8664;
    std::memcpy(pe + 68, &machine, 2);
    const std::uint16_t magic = 0x20b;
    std::memcpy(pe + 64 + 24, &magic, 2);
    std::vector<PeHit> hits;
    ExtractPe(pe, sizeof(pe), 0x400000, hits);
    Expect(!hits.empty() && hits[0].pe32plus && hits[0].machine == 0x8664,
           "ExtractPe finds a PE32+ MZ header at offset 0",
           hits.empty() ? "miss" : "ok");
    unsigned char notpe[16];
    std::memset(notpe, 0x41, sizeof(notpe));
    std::vector<PeHit> none;
    ExtractPe(notpe, sizeof(notpe), 0, none);
    Expect(none.empty(), "ExtractPe ignores non-MZ buffers", "pe");
  }
  {
    const char* blob = "{\"layer\":\"DIMS\",\"zoom\":1.25}";
    InspectConfig cfg;
    std::vector<JsonHit> hits;
    ExtractJson(reinterpret_cast<const std::uint8_t*>(blob), std::strlen(blob), 0x8000,
                cfg, hits);
    Expect(!hits.empty() && hits[0].text.find("layer") != std::string::npos,
           "ExtractJson finds a state object",
           hits.empty() ? "miss" : hits[0].text.c_str());
  }
  {
    unsigned char buf[40 + 32 * 32 * 4];
    std::memset(buf, 0, sizeof(buf));
    buf[0] = 40;
    const std::int32_t w = 32, h = 32;
    std::memcpy(buf + 4, &w, 4);
    std::memcpy(buf + 8, &h, 4);
    buf[12] = 1;
    buf[14] = 32;
    const std::uint32_t img = 32 * 32 * 4;
    std::memcpy(buf + 20, &img, 4);
    buf[40 + 2] = 200;  // first pixel R in BGRA
    InspectConfig cfg;
    std::vector<DibHit> hits;
    ExtractDibs(buf, sizeof(buf), 0x9000, cfg, hits);
    Expect(!hits.empty() && hits[0].width == 32 && hits[0].height == 32 &&
               hits[0].bit_count == 32 && hits[0].rgba.size() == 32 * 32 * 4,
           "ExtractDibs keeps a real 32bpp BITMAPINFOHEADER + pixels",
           hits.empty() ? "miss" : "ok");
  }
  {
    unsigned char buf[64];
    std::memset(buf, 0, sizeof(buf));
    buf[0] = 40;
    buf[4] = 32;
    buf[8] = 32;
    buf[12] = 1;
    buf[14] = 32;
    buf[16] = 0xFF;
    buf[17] = 0xFF;
    buf[18] = 0xFF;
    buf[19] = 0xFF;
    InspectConfig cfg;
    std::vector<DibHit> hits;
    ExtractDibs(buf, sizeof(buf), 0xA000, cfg, hits);
    Expect(hits.empty(), "ExtractDibs ignores 40-byte blobs with garbage compression",
           hits.empty() ? "ok" : "false-positive");
  }
  {
    unsigned char buf[54 + 24 * 24 * 4];
    std::memset(buf, 0, sizeof(buf));
    buf[0] = 'B';
    buf[1] = 'M';
    const std::uint32_t off = 54;
    std::memcpy(buf + 10, &off, 4);
    buf[14] = 40;
    const std::int32_t w = 24, h = 24;
    std::memcpy(buf + 18, &w, 4);
    std::memcpy(buf + 22, &h, 4);
    buf[26] = 1;
    buf[28] = 32;
    const std::uint32_t img = 24 * 24 * 4;
    std::memcpy(buf + 34, &img, 4);
    buf[54 + 2] = 180;
    InspectConfig cfg;
    std::vector<DibHit> hits;
    ExtractDibs(buf, sizeof(buf), 0xB000, cfg, hits);
    Expect(!hits.empty() && hits[0].width == 24 && hits[0].bit_count == 32 &&
               hits[0].rgba.size() == 24 * 24 * 4,
           "ExtractDibs follows a BM BITMAPFILEHEADER to pixels",
           hits.empty() ? "miss" : "ok");
  }
  {
    TokenSnapshot self;
    self.pid = 10;
    self.integrity = Integrity::Medium;
    self.identity = Identity::User;
    TokenSnapshot same = self;
    Expect(AllowInspect(self, same).code == PrivilegeCode::Ok,
           "self inspect is allowed", "token-wall");
    TokenSnapshot sys;
    sys.pid = 4;
    sys.integrity = Integrity::System;
    sys.identity = Identity::System;
    sys.system = true;
    sys.image = L"spoolsv.exe";
    Expect(AllowInspect(self, sys).code == PrivilegeCode::DeniedEscalate,
           "user cannot inspect SYSTEM (downgrade wall)",
           PrivilegeCodeName(AllowInspect(self, sys).code));
    TokenSnapshot ti;
    ti.pid = 99;
    ti.trusted_installer = true;
    ti.identity = Identity::TrustedInstaller;
    ti.image = L"TrustedInstaller.exe";
    Expect(AllowInspect(self, ti).code == PrivilegeCode::DeniedProtected,
           "TrustedInstaller target is denied-protected",
           PrivilegeCodeName(AllowInspect(self, ti).code));
    TokenSnapshot ppl;
    ppl.pid = 7;
    ppl.protected_process = true;
    ppl.image = L"MsMpEng.exe";
    Expect(AllowInspect(self, ppl).code == PrivilegeCode::DeniedProtected,
           "PPL target is denied-protected",
           PrivilegeCodeName(AllowInspect(self, ppl).code));
    TokenSnapshot acad;
    acad.pid = 4242;
    acad.integrity = Integrity::Medium;
    acad.identity = Identity::User;
    acad.image = L"acad.exe";
    Expect(AllowInspect(self, acad).code == PrivilegeCode::Ok,
           "same-integrity GUI process is allowed",
           PrivilegeCodeName(AllowInspect(self, acad).code));
  }
  {
    Result<TokenSnapshot> own = QueryOwnToken();
    Expect(own.ok() && own.value().queried, "QueryOwnToken succeeds",
           own.ok() ? own.value().detail.c_str() : PrivilegeCodeName(own.error().code));
    Expect(own.ok() && AllowInspect(own.value(), own.value()).code == PrivilegeCode::Ok,
           "own token vs own token is allowed", "token-wall");
  }
  {
    const auto procs = ProcessPerception::ListProcesses();
    Expect(!procs.empty(), "ListProcesses returns a live /proc or Toolhelp list",
           "list");
    bool self_seen = false;
#if defined(_WIN32)
    const std::uint32_t self_pid = GetCurrentProcessId();
#else
    const std::uint32_t self_pid = static_cast<std::uint32_t>(getpid());
#endif
    for (const auto& p : procs) {
      if (p.pid == self_pid) self_seen = true;
    }
    Expect(self_seen, "ListProcesses includes this process", "list");
  }
  {
    ProcessPerception perception;
    const PerceptionSnapshot snap = perception.Snapshot();
    Expect(snap.process.pid != 0, "Snapshot reports a real PID on this OS",
           "pid");
  }
  {
    char* marker = new char[64];
    std::memset(marker, 0, 64);
    std::memcpy(marker, "SECDOGIE_LOOP_MARKER_v1", 23);
    LoopConfig cfg;
#if defined(_WIN32)
    cfg.target_pid = GetCurrentProcessId();
#else
    cfg.target_pid = static_cast<std::uint32_t>(getpid());
#endif
    HybridControlLoop loop(ProcessPerception{}, cfg);
    LoopAction act;
    act.id = "read-marker";
    act.kind = ActionKind::Read;
    act.selector.name = L"SECDOGIE_LOOP_MARKER_v1";
    const LoopStep st = loop.Run(act);
    Expect(st.status == StepStatus::Passed,
           "hybrid loop memory fallback finds a live heap marker (read)",
           StepStatusName(st.status));
    Expect(st.mode == PerceptionMode::Memory,
           "hybrid loop mode is Memory when UIA is empty",
           st.mode == PerceptionMode::Uia
               ? "uia"
               : (st.mode == PerceptionMode::Memory ? "memory" : "vision"));
    delete[] marker;
  }
}
