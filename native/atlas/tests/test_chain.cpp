#include "mct_command.h"
#include "process_chain.h"
#include "process_perception.h"

#include <string>
#include <vector>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

using namespace secdogie::atlas;

extern int g_failed;
extern int g_passed;
void Expect(bool cond, const char* name, const char* detail);

void RunChainTests() {
  {
    ListedProcess a;
    a.pid = 10;
    a.image = L"atlas_target";
    ListedProcess b;
    b.pid = 11;
    b.ppid = 10;
    b.image = L"atlas_target";
    b.cmdline = L"--report";
    ListedProcess excel;
    excel.pid = 20;
    excel.image = L"EXCEL.EXE";
    Expect(ProcessFamily(a) == "atlas", "atlas_target family", ProcessFamily(a).c_str());
    Expect(ProcessFamily(b) == "atlas", "--report is atlas family", ProcessFamily(b).c_str());
    Expect(ProcessFamily(excel) == "office", "excel is office family", ProcessFamily(excel).c_str());
    ListedProcess sh;
    sh.pid = 3;
    sh.image = L"bash";
    sh.cmdline = L"bash -c 'eval --report snapshot with atlas_target'";
    Expect(ProcessFamily(sh).empty(), "shell cmdline mentioning --report is not atlas family",
           ProcessFamily(sh).c_str());
  }
  {
    ListedProcess parent;
    parent.pid = 100;
    parent.ppid = 1;
    parent.image = L"atlas_target";
    parent.readable = true;
    parent.rss_kb = 1024;
    ListedProcess child;
    child.pid = 101;
    child.ppid = 100;
    child.image = L"atlas_target";
    child.cmdline = L"/tmp/atlas_target --report";
    child.readable = true;
    child.rss_kb = 512;
    ListedProcess other;
    other.pid = 200;
    other.ppid = 2;
    other.image = L"bash";
    other.readable = true;
    const std::vector<ListedProcess> all{parent, child, other};
    const auto chain = BuildChain(100, all, {});
    bool has_child = false;
    bool has_bash = false;
    for (const auto& m : chain) {
      if (m.pid == 101 && m.rel == ChainRel::Child) has_child = true;
      if (m.pid == 200) has_bash = true;
    }
    Expect(!chain.empty() && chain[0].rel == ChainRel::Self, "chain root is self",
           chain.empty() ? "empty" : ChainRelName(chain[0].rel));
    Expect(has_child, "chain includes report child", has_child ? "child" : "miss");
    Expect(!has_bash, "chain does not swallow unrelated bash", has_bash ? "leaked" : "ok");
    ListedProcess mct;
    mct.pid = 1;
    mct.image = L"atlas_mct";
    parent.ppid = 1;
    const auto chained = BuildChain(100, {parent, child, mct}, {});
    bool has_mct = false;
    for (const auto& m : chained) {
      if (m.pid == 1) has_mct = true;
    }
    Expect(!has_mct, "chain skips atlas_mct parent", has_mct ? "leaked" : "ok");
  }
  {
    ListedProcess cad;
    cad.pid = 5;
    cad.ppid = 1;
    cad.image = L"acad.exe";
    cad.readable = true;
    ListedProcess xls;
    xls.pid = 11;
    xls.ppid = 9;
    xls.image = L"EXCEL.EXE";
    xls.readable = false;
    const auto chain = BuildChain(5, {cad, xls}, {11});
    bool linked_unread = false;
    for (const auto& m : chain) {
      if (m.pid == 11 && m.rel == ChainRel::Linked && !m.readable) linked_unread = true;
    }
    Expect(linked_unread, "operator link chains unread excel to CAD",
           linked_unread ? "linked" : "miss");
    ChainMember gone;
    gone.pid = 11;
    gone.ppid = 9;
    gone.image = L"EXCEL.EXE";
    gone.rel = ChainRel::Linked;
    gone.readable = true;
    std::vector<ChainMember> live = BuildChain(5, {cad}, {});
    MergeLastKnown(live, {gone});
    bool kept = false;
    for (const auto& m : live) {
      if (m.pid == 11 && !m.readable && m.rel == ChainRel::Linked) kept = true;
    }
    Expect(kept, "vanished report workbook stays on chain as isolated",
           kept ? "kept" : "dropped");
    ChainMember mct;
    mct.pid = 1;
    mct.image = L"atlas_mct";
    mct.rel = ChainRel::Parent;
    MergeLastKnown(live, {mct});
    bool leaked = false;
    for (const auto& m : live) {
      if (m.pid == 1) leaked = true;
    }
    Expect(!leaked, "merge skips atlas_mct even if it was last-known parent",
           leaked ? "leaked" : "ok");
  }
  {
    ListedProcess cad;
    cad.pid = 5;
    cad.image = L"acad.exe";
    cad.readable = true;
    ListedProcess core;
    core.pid = 6;
    core.image = L"accoreconsole.exe";
    core.readable = true;
    const auto chain = BuildChain(5, {cad, core}, {});
    bool fam = false;
    for (const auto& m : chain) {
      if (m.pid == 6 && m.rel == ChainRel::Family) fam = true;
    }
    Expect(fam, "acad + accoreconsole family chain", fam ? "family" : "miss");
  }
  {
    std::string last = "{\"ok\":true,\"pid\":9,\"image\":\"stale\"}";
    InspectConfig cfg;
    cfg.max_bytes = 1024 * 1024;
    InspectAttempt a = InspectWithRetry(3999999, cfg, L"", &last, 2);
    Expect(a.stale && a.json.find("stale") != std::string::npos,
           "dead pid falls back to last-known (isolated)", a.detail.c_str());
    Expect(!a.ok, "dead pid is not a live inspect", a.ok ? "ok" : "isolated");
    Expect(a.attempts >= 2, "retries before last-known", std::to_string(a.attempts).c_str());
  }
  {
    std::string arg;
    Expect(ParseMctLine("chain", &arg) == MctOp::Chain, "parse chain", "op");
    Expect(ParseMctLine("串联", &arg) == MctOp::Chain, "parse 串联", "op");
    Expect(ParseMctLine("link 42", &arg) == MctOp::Link && arg == "42", "parse link", arg.c_str());
    Expect(ParseMctLine("job report", &arg) == MctOp::Job, "parse job report", arg.c_str());
    Expect(ParseMctLine("报表", &arg) == MctOp::Job, "parse 报表", "op");
  }
  {
    MctState st;
    const std::string help = ExecMctLine(st, "help");
    Expect(help.find("job report") != std::string::npos && help.find("chain") != std::string::npos,
           "help lists chain and job report", help.c_str());
  }
#if !defined(_WIN32)
  {
    const pid_t child = fork();
    if (child == 0) {
      for (;;) pause();
    } else if (child > 0) {
      usleep(120000);
      const auto all = ProcessPerception::ListProcesses();
      const auto chain = BuildChain(static_cast<std::uint32_t>(getpid()), all, {});
      bool found = false;
      for (const auto& m : chain) {
        if (m.pid == static_cast<std::uint32_t>(child) && m.rel == ChainRel::Child) found = true;
      }
      kill(child, SIGKILL);
      waitpid(child, nullptr, 0);
      Expect(found, "live fork child appears on chain", found ? "child" : "miss");
    }
  }
#endif
  {
    std::string last = "{\"ok\":true,\"pid\":1}";
    InspectConfig cfg;
    JobResult job = RunReportJob(3999999, {}, cfg, &last);
    Expect(job.status == "failed" || job.status == "isolated" || job.status == "passed",
           "missing source does not crash the job runner", job.status.c_str());
    std::vector<ChainMember> prev;
    ChainMember self;
    self.pid = 424242;
    self.image = L"atlas_target";
    self.rel = ChainRel::Self;
    self.readable = true;
    ChainMember report;
    report.pid = 424243;
    report.ppid = 424242;
    report.image = L"atlas_target";
    report.cmdline = L"--report";
    report.rel = ChainRel::Child;
    report.readable = true;
    prev.push_back(self);
    prev.push_back(report);
    std::string snap = "{\"ok\":true,\"pid\":424242,\"image\":\"stale-source\"}";
    JobResult gone = RunReportJob(424242, {}, cfg, &snap, &prev);
    bool has_report_stage = false;
    for (const auto& s : gone.stages) {
      if (s.pid == 424243 && s.status == StageStatus::Isolated) has_report_stage = true;
    }
    Expect(gone.status == "isolated" || gone.status == "passed" || gone.status == "failed",
           "gone-pid job still returns", gone.status.c_str());
    if (gone.source_pid == 424242) {
      Expect(has_report_stage, "gone report child is isolated, not dropped",
             has_report_stage ? "isolated" : "dropped");
      Expect(gone.status == "isolated", "last-known source keeps the job isolated not failed",
             gone.status.c_str());
    }
  }
}
