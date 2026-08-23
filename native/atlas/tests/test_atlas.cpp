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

int g_failed = 0;
int g_passed = 0;

void Expect(bool cond, const char* name, const char* detail) {
  if (cond) {
    std::printf("  PASS  %s\n", name);
    ++g_passed;
  } else {
    std::printf("  FAIL  %s — %s\n", name, detail);
    ++g_failed;
  }
}

void RunMemoryInspectorTests();

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
    Selector by_name;
    by_name.name = L"zoom extents";
    Expect(ProcessPerception::Find(roots, by_name) == &roots[0],
           "Find name is case-insensitive", "perception");
    Selector empty;
    Expect(ProcessPerception::Find(roots, empty) == nullptr,
           "empty selector matches nothing", "perception");
  }
  {
    Framebuffer a, d;
    a.width = a.height = 16;
    a.bgra.assign(16 * 16 * 4, 40);
    d = a;
    d.width = 8;
    Expect(PixelDiff::ChangedRatio(a, d) == 1.0, "size mismatch is fully changed",
           "pixel-diff");
  }
  {
    HybridControlLoop loop(ProcessPerception{}, LoopConfig{});
    PerceptionSnapshot prev;
    ControlNode btn;
    btn.role = ControlRole::Button;
    btn.name = L"Zoom Extents";
    btn.automation_id = L"ID_ZOOM_EXTENTS";
    btn.bounds = {10, 10, 80, 24};
    prev.controls.push_back(btn);
    loop.SetLastSnapshot(std::move(prev));

    Framebuffer dark, light;
    dark.width = light.width = 8;
    dark.height = light.height = 8;
    dark.bgra.assign(8 * 8 * 4, 10);
    light.bgra.assign(8 * 8 * 4, 200);
    int n = 0;
    loop.SetCapture([&](const Rect&) -> Result<Framebuffer> {
      ++n;
      return n == 1 ? dark : light;
    });
    loop.SetExecute([](const ControlNode&, const LoopAction&) {
      return PrivilegeError{PrivilegeCode::Ok, "ok"};
    });
    LoopAction act;
    act.id = "zoom";
    act.selector.automation_id = L"ID_ZOOM_EXTENTS";
    const LoopStep st = loop.Run(act);
    Expect(st.status == StepStatus::Passed,
           "fallback uses previous snapshot then pixel-diff",
           StepStatusName(st.status));
    Expect(st.mode == PerceptionMode::VisionFallback, "fallback mode is vision",
           st.mode == PerceptionMode::Uia ? "uia" : "vision");
    Expect(loop.last_snapshot().controls.size() == 1,
           "empty current snapshot does not wipe last-known",
           "last_ overwritten");
  }
  {
    HybridControlLoop loop(ProcessPerception{}, LoopConfig{});
    LoopAction save;
    save.id = "save";
    save.high_risk = true;
    const LoopStep st = loop.Run(save);
    Expect(st.status == StepStatus::Blocked,
           "high-risk blocked without operator confirm",
           StepStatusName(st.status));
  }
  {
    HybridControlLoop loop(ProcessPerception{}, LoopConfig{});
    PerceptionSnapshot prev;
    ControlNode btn;
    btn.role = ControlRole::Button;
    btn.name = L"Zoom Extents";
    btn.automation_id = L"ID_ZOOM_EXTENTS";
    btn.bounds = {10, 10, 80, 24};
    prev.controls.push_back(btn);
    loop.SetLastSnapshot(std::move(prev));
    Framebuffer flat;
    flat.width = flat.height = 8;
    flat.bgra.assign(8 * 8 * 4, 40);
    loop.SetCapture([&](const Rect&) -> Result<Framebuffer> { return flat; });
    loop.SetExecute([](const ControlNode&, const LoopAction&) {
      return PrivilegeError{PrivilegeCode::Ok, "ok"};
    });
    LoopAction act;
    act.id = "zoom";
    act.selector.automation_id = L"ID_ZOOM_EXTENTS";
    loop.config().max_retries = 0;
    const LoopStep st = loop.Run(act);
    Expect(st.status == StepStatus::Failed,
           "no-mutation is failed, not passed", StepStatusName(st.status));
  }
  {
    HybridControlLoop loop(ProcessPerception{}, LoopConfig{});
    PerceptionSnapshot prev;
    ControlNode btn;
    btn.role = ControlRole::Button;
    btn.name = L"Zoom Extents";
    btn.automation_id = L"ID_ZOOM_EXTENTS";
    btn.bounds = {10, 10, 80, 24};
    prev.controls.push_back(btn);
    loop.SetLastSnapshot(std::move(prev));
    Framebuffer flat;
    flat.width = flat.height = 8;
    flat.bgra.assign(8 * 8 * 4, 40);
    loop.SetCapture([&](const Rect&) -> Result<Framebuffer> { return flat; });
    loop.SetExecute([](const ControlNode&, const LoopAction&) {
      return PrivilegeError{PrivilegeCode::Failed, "invoke refused"};
    });
    LoopAction act;
    act.id = "zoom";
    act.selector.automation_id = L"ID_ZOOM_EXTENTS";
    const LoopStep st = loop.Run(act);
    Expect(st.status == StepStatus::Failed &&
               st.detail.find("execute failed") != std::string::npos,
           "execute failure is Failed, not retried as no-mutation",
           st.detail.c_str());
  }
  {
    // Handle open on a fake pid is access-denied or unsupported — never a
    // write handle.
    auto h = ReadOnlyProcessHandle::Open(1, kProcessVmWrite);
    Expect(!h.ok() && h.error().code == PrivilegeCode::DeniedWrite,
           "Open with VM_WRITE refused before syscall",
           h.ok() ? "ok" : PrivilegeCodeName(h.error().code));
  }

  RunMemoryInspectorTests();

  std::printf("\n%d passed, %d failed\n", g_passed, g_failed);
  return g_failed ? 1 : 0;
}
