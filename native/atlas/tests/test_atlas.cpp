// Self-contained unit tests. Build with the atlas static lib.
//
//   cmake -S . -B build && cmake --build build --target atlas_test
//   ./build/atlas_test
//
// Covers: read-only enforcement, TrustedInstaller refusal, allowlist exact
// match, SYSTEM plan refusals, pixel-diff, selector matching. Win32 calls
// that need a desktop are skipped off Windows (the decision core still runs).

#include "hybrid_control_loop.h"
#include "privilege_manager.h"
#include "process_perception.h"

#include <cstdio>
#include <string>
#include <vector>

using namespace secdogie::atlas;

static int g_failed = 0;
static int g_passed = 0;

static void Expect(bool cond, const char* name, const char* detail) {
  if (cond) {
    std::printf("  PASS  %s\n", name);
    ++g_passed;
  } else {
    std::printf("  FAIL  %s — %s\n", name, detail);
    ++g_failed;
  }
}

int main() {
  std::printf("atlas_test\n");

  {
    const AccessDecision ok =
        EnforceReadOnly(kProcessQueryInformation | kProcessVmRead);
    Expect(!ok.refused && ok.granted == (kProcessQueryInformation | kProcessVmRead),
           "query+vm_read granted", PrivilegeCodeName(ok.code));
  }
  {
    const AccessDecision w = EnforceReadOnly(kProcessVmWrite);
    Expect(w.refused && w.code == PrivilegeCode::DeniedWrite && w.granted == 0,
           "PROCESS_VM_WRITE refused", PrivilegeCodeName(w.code));
  }
  {
    const AccessDecision all = EnforceReadOnly(kProcessAllAccess);
    Expect(all.refused && all.granted == 0,
           "PROCESS_ALL_ACCESS refused (not narrowed)", PrivilegeCodeName(all.code));
  }
  {
    PrivilegeManager pm;
    const PrivilegeError ti = pm.TryImpersonateTrustedInstaller();
    Expect(ti.code == PrivilegeCode::RefusedIdentity,
           "TrustedInstaller impersonation refused", PrivilegeCodeName(ti.code));
    const PrivilegeError edr = pm.TryEdrEvasion();
    Expect(edr.code == PrivilegeCode::RefusedIdentity, "anti-EDR refused",
           PrivilegeCodeName(edr.code));
  }
  {
    Expect(!PrivilegeManager::IsAllowlisted(L"sc stop spooler", {}),
           "empty allowlist permits nothing", "allowlist");
    std::vector<std::wstring> al{L"sc stop spooler"};
    Expect(PrivilegeManager::IsAllowlisted(L"sc stop spooler", al),
           "exact allowlist match", "allowlist");
    Expect(!PrivilegeManager::IsAllowlisted(L"sc stop spooler && calc", al),
           "allowlist is not a prefix match", "allowlist");
  }
  {
    PrivilegeManager pm({L"notepad.exe"});
    const LaunchDecision d = pm.PlanSystemLaunch(L"notepad.exe");
#if defined(_WIN32)
    // On Windows without elevation this is not-elevated; with elevation it
    // may proceed as far as session. Either way it is never RefusedIdentity.
    Expect(d.code != PrivilegeCode::RefusedIdentity,
           "SYSTEM plan is not TI impersonation", PrivilegeCodeName(d.code));
#else
    Expect(d.code == PrivilegeCode::Unsupported, "SYSTEM plan unsupported off Windows",
           PrivilegeCodeName(d.code));
#endif
  }
  {
    Framebuffer a, b, c;
    a.width = b.width = c.width = 16;
    a.height = b.height = c.height = 16;
    a.bgra.assign(16 * 16 * 4, 40);
    b.bgra = a.bgra;
    c.bgra.assign(16 * 16 * 4, 200);
    Expect(PixelDiff::ChangedRatio(a, b) < 0.001, "identical frames diff ~0",
           "pixel-diff");
    Expect(PixelDiff::ChangedRatio(a, c) > 0.012, "mutated frame exceeds threshold",
           "pixel-diff");
    Expect(PixelDiff::Hash(a) == PixelDiff::Hash(b) &&
               PixelDiff::Hash(a) != PixelDiff::Hash(c),
           "hash distinguishes mutation", "hash");
  }
  {
    ControlNode btn;
    btn.role = ControlRole::Button;
    btn.name = L"Zoom Extents";
    btn.automation_id = L"ID_ZOOM_EXTENTS";
    btn.bounds = {10, 10, 80, 24};
    std::vector<ControlNode> roots{btn};
    Selector s;
    s.automation_id = L"ID_ZOOM_EXTENTS";
    Expect(ProcessPerception::Find(roots, s) == &roots[0],
           "Find by automationId", "perception");
    Selector miss;
    miss.automation_id = L"NOPE";
    Expect(ProcessPerception::Find(roots, miss) == nullptr, "Find miss returns null",
           "perception");
  }
  {
    // Handle open on a fake pid is access-denied or unsupported — never a
    // write handle.
    auto h = ReadOnlyProcessHandle::Open(1, kProcessVmWrite);
    Expect(!h.ok() && h.error().code == PrivilegeCode::DeniedWrite,
           "Open with VM_WRITE refused before syscall",
           h.ok() ? "ok" : PrivilegeCodeName(h.error().code));
  }

  std::printf("\n%d passed, %d failed\n", g_passed, g_failed);
  return g_failed ? 1 : 0;
}
