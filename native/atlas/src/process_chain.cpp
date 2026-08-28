#include "process_chain.h"

#include "inspect_json.h"
#include "utf.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <unistd.h>
#endif

namespace secdogie::atlas {
namespace {

std::string Lower(std::string s) {
  for (char& c : s) {
    if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);
  }
  return s;
}

std::string Basename(const std::wstring& path) {
  const std::string s = WideToUtf8(path);
  const auto slash = s.find_last_of("/\\");
  return slash == std::string::npos ? s : s.substr(slash + 1);
}

std::string JsonEsc(const std::string& s) {
  std::string o;
  o.push_back('"');
  for (unsigned char c : s) {
    if (c == '"' || c == '\\') {
      o.push_back('\\');
      o.push_back(static_cast<char>(c));
    } else if (c == '\n') {
      o += "\\n";
    } else if (c < 0x20) {
      char b[8];
      std::snprintf(b, sizeof(b), "\\u%04x", c);
      o += b;
    } else {
      o.push_back(static_cast<char>(c));
    }
  }
  o.push_back('"');
  return o;
}

std::string JsonW(const std::wstring& w) { return JsonEsc(WideToUtf8(w)); }

bool ContainsId(const std::vector<std::uint32_t>& ids, std::uint32_t pid) {
  for (std::uint32_t x : ids) {
    if (x == pid) return true;
  }
  return false;
}

const ListedProcess* FindPid(const std::vector<ListedProcess>& all, std::uint32_t pid) {
  for (const auto& p : all) {
    if (p.pid == pid) return &p;
  }
  return nullptr;
}

bool IsInspectorName(const std::wstring& image) {
  const std::string b = Lower(Basename(image));
  return b.find("atlas_mct") != std::string::npos || b.find("atlas_inspect") != std::string::npos;
}

bool IsInspectorImage(const ListedProcess& p) { return IsInspectorName(p.image); }

bool LooksReport(const ListedProcess& p) {
  const std::string cmd = Lower(WideToUtf8(p.cmdline));
  const std::string img = Lower(Basename(p.image));
  return cmd.find("--report") != std::string::npos || img.find("atlas_report") != std::string::npos;
}

}  // namespace

const char* ChainRelName(ChainRel r) noexcept {
  switch (r) {
    case ChainRel::Self: return "self";
    case ChainRel::Parent: return "parent";
    case ChainRel::Child: return "child";
    case ChainRel::Sibling: return "sibling";
    case ChainRel::Session: return "session";
    case ChainRel::Family: return "family";
    case ChainRel::Linked: return "linked";
  }
  return "self";
}

const char* StageStatusName(StageStatus s) noexcept {
  switch (s) {
    case StageStatus::Pending: return "pending";
    case StageStatus::Running: return "running";
    case StageStatus::Passed: return "passed";
    case StageStatus::Isolated: return "isolated";
    case StageStatus::Failed: return "failed";
    case StageStatus::Skipped: return "skipped";
  }
  return "pending";
}

void SleepMs(int ms) {
  if (ms <= 0) return;
#if defined(_WIN32)
  Sleep(static_cast<DWORD>(ms));
#else
  usleep(static_cast<useconds_t>(ms) * 1000);
#endif
}

std::string ProcessFamily(const ListedProcess& p) {
  const std::string b = Lower(Basename(p.image));
  if (b.find("atlas_target") != std::string::npos || b.find("atlas_report") != std::string::npos) {
    return "atlas";
  }
  if (b.find("acad") != std::string::npos || b.find("accoreconsole") != std::string::npos) {
    return "acad";
  }
  if (b.find("excel") != std::string::npos || b.find("winword") != std::string::npos ||
      b.find("powerpnt") != std::string::npos || b.find("wps") != std::string::npos) {
    return "office";
  }
  if (b.find("chrome") != std::string::npos || b.find("msedge") != std::string::npos ||
      b.find("firefox") != std::string::npos) {
    return "browser";
  }
  return {};
}

std::vector<ChainMember> BuildChain(std::uint32_t root_pid,
                                    const std::vector<ListedProcess>& all,
                                    const std::vector<std::uint32_t>& linked) {
  std::vector<ChainMember> out;
  const ListedProcess* root = FindPid(all, root_pid);
  if (!root) return out;
  const std::string fam = ProcessFamily(*root);

  auto push = [&](const ListedProcess& p, ChainRel rel) {
    for (const auto& m : out) {
      if (m.pid == p.pid) return;
    }
    ChainMember m;
    m.pid = p.pid;
    m.ppid = p.ppid;
    m.session_id = p.session_id;
    m.image = p.image;
    m.cmdline = p.cmdline;
    m.readable = p.readable;
    m.rss_kb = p.rss_kb;
    m.rel = rel;
    out.push_back(std::move(m));
  };

  push(*root, ChainRel::Self);
  for (const auto& p : all) {
    if (p.pid == root->pid) continue;
    if (IsInspectorImage(p)) continue;
    if (root->ppid != 0 && p.pid == root->ppid) {
      push(p, ChainRel::Parent);
      continue;
    }
    if (p.ppid != 0 && p.ppid == root->pid) {
      push(p, ChainRel::Child);
      continue;
    }
    if (p.ppid != 0 && root->ppid != 0 && p.ppid == root->ppid) {
      push(p, ChainRel::Sibling);
      continue;
    }
    if (!fam.empty() && ProcessFamily(p) == fam) {
      if (p.session_id != 0 && p.session_id == root->session_id) {
        push(p, ChainRel::Session);
      } else {
        push(p, ChainRel::Family);
      }
      continue;
    }
    if (ContainsId(linked, p.pid)) {
      push(p, ChainRel::Linked);
    }
  }
  return out;
}

void MergeLastKnown(std::vector<ChainMember>& live, const std::vector<ChainMember>& last) {
  if (last.empty()) return;
  auto has_pid = [&](std::uint32_t pid) {
    for (const auto& m : live) {
      if (m.pid == pid) return true;
    }
    return false;
  };
  const std::uint32_t root = live.empty() ? 0 : live[0].pid;
  const std::uint32_t root_ppid = live.empty() ? 0 : live[0].ppid;
  if (live.empty()) {
    for (const auto& old : last) {
      if (IsInspectorName(old.image)) continue;
      ChainMember gone = old;
      gone.readable = false;
      live.push_back(std::move(gone));
    }
    return;
  }
  for (const auto& old : last) {
    if (has_pid(old.pid)) continue;
    if (IsInspectorName(old.image)) continue;
    const bool related = old.pid == root || old.ppid == root ||
                         (root_ppid != 0 && old.ppid == root_ppid) ||
                         old.rel == ChainRel::Linked || old.rel == ChainRel::Family;
    if (!related) continue;
    ChainMember gone = old;
    gone.readable = false;
    live.push_back(std::move(gone));
  }
}

InspectAttempt InspectWithRetry(std::uint32_t pid, const InspectConfig& cfg,
                                const std::wstring& find, const std::string* last_known,
                                int retries) {
  InspectAttempt a;
  if (pid == 0) {
    a.detail = "no pid";
    if (last_known && last_known->find("\"ok\":true") != std::string::npos) {
      a.stale = true;
      a.json = *last_known;
      a.detail = "no pid — last-known snapshot";
    }
    return a;
  }
  if (retries < 1) retries = 1;
  for (int i = 0; i < retries; ++i) {
    ++a.attempts;
    const std::string json = DumpInspectJson(pid, cfg, find);
    if (json.find("\"ok\":true") != std::string::npos) {
      a.ok = true;
      a.json = json;
      a.detail = "inspect ok";
      return a;
    }
    a.detail = json.size() > 180 ? json.substr(0, 180) : json;
    if (i + 1 < retries) SleepMs(80 << i);
  }
  if (last_known && last_known->find("\"ok\":true") != std::string::npos) {
    a.stale = true;
    a.json = *last_known;
    a.detail = "inspect jitter — last-known snapshot (isolated)";
  }
  return a;
}

JobResult RunReportJob(std::uint32_t source_pid, const std::vector<std::uint32_t>& linked,
                       const InspectConfig& cfg, const std::string* last_known,
                       const std::vector<ChainMember>* last_chain) {
  JobResult job;
  job.name = "report";
  const auto all = ProcessPerception::ListProcesses();
  const ListedProcess* src = FindPid(all, source_pid);
  if (!src) {
    for (const auto& p : all) {
      if (!p.readable) continue;
      if (IsInspectorImage(p)) continue;
      if (ProcessFamily(p) != "atlas") continue;
      if (LooksReport(p)) continue;
      src = &p;
      source_pid = p.pid;
      break;
    }
  }
  std::vector<ChainMember> chain;
  if (src) {
    job.source_pid = src->pid;
    chain = BuildChain(src->pid, all, linked);
  } else if (last_chain && !last_chain->empty()) {
    job.source_pid = (*last_chain)[0].pid;
    chain = *last_chain;
    for (auto& m : chain) m.readable = false;
  } else {
    JobStage st;
    st.id = "source";
    st.role = "source";
    st.status = StageStatus::Failed;
    st.detail = "no source process for report job";
    job.stages.push_back(st);
    job.status = "failed";
    return job;
  }
  if (last_chain) MergeLastKnown(chain, *last_chain);

  auto role_of = [](const ChainMember& m) -> std::string {
    if (m.rel == ChainRel::Self) return "source";
    const std::string cmd = Lower(WideToUtf8(m.cmdline));
    const std::string img = Lower(Basename(m.image));
    if (cmd.find("--report") != std::string::npos || img.find("atlas_report") != std::string::npos ||
        img.find("excel") != std::string::npos || img.find("winword") != std::string::npos) {
      return "report";
    }
    return "related";
  };

  int passed = 0;
  int isolated = 0;
  int stale_ok = 0;
  for (const auto& m : chain) {
    JobStage st;
    st.id = role_of(m) + "-" + std::to_string(m.pid);
    st.role = role_of(m);
    st.pid = m.pid;
    st.image = m.image;
    if (st.role == "source") st.find = L"LAYER_DIMS";
    else if (st.role == "report") st.find = L"报表";
    st.status = StageStatus::Running;

    const ListedProcess* live = FindPid(all, m.pid);
    if (!live) {
      const std::string* cache = (st.role == "source") ? last_known : nullptr;
      InspectAttempt a = InspectWithRetry(m.pid, cfg, st.find, cache, 1);
      st.attempts = a.attempts;
      st.stale = a.stale;
      st.status = StageStatus::Isolated;
      st.detail = a.stale ? a.detail : "process gone / tunnel drop — isolated, job continues";
      if (a.stale) {
        ++stale_ok;
        if (st.role == "source" && job.last_snap.empty()) job.last_snap = a.json;
      }
      ++isolated;
      job.stages.push_back(std::move(st));
      continue;
    }

    if (!m.readable || !live->readable) {
      st.status = StageStatus::Isolated;
      st.detail = "unreadable — isolated, job continues";
      ++isolated;
      job.stages.push_back(std::move(st));
      continue;
    }

    const std::string* cache = (st.role == "source") ? last_known : nullptr;
    InspectAttempt a = InspectWithRetry(m.pid, cfg, st.find, cache, 3);
    st.attempts = a.attempts;
    st.stale = a.stale;
    if (a.ok) {
      st.status = StageStatus::Passed;
      st.detail = "inspect ok";
      if (st.role == "source") job.last_snap = a.json;
      ++passed;
    } else if (a.stale) {
      st.status = StageStatus::Isolated;
      st.detail = a.detail;
      if (st.role == "source" && job.last_snap.empty()) job.last_snap = a.json;
      ++isolated;
      ++stale_ok;
    } else {
      st.status = StageStatus::Isolated;
      st.detail = a.detail.empty() ? "inspect failed — isolated" : a.detail;
      ++isolated;
    }
    job.stages.push_back(std::move(st));
  }

  if (passed > 0 && isolated == 0) job.status = "passed";
  else if (passed + stale_ok > 0) job.status = "isolated";
  else job.status = "failed";
  return job;
}

std::string DumpChainJson(const std::vector<ChainMember>& chain) {
  std::string o = "[";
  for (std::size_t i = 0; i < chain.size(); ++i) {
    if (i) o.push_back(',');
    const auto& m = chain[i];
    o += "{\"pid\":";
    o += std::to_string(m.pid);
    o += ",\"ppid\":";
    o += std::to_string(m.ppid);
    o += ",\"session\":";
    o += std::to_string(m.session_id);
    o += ",\"rss_kb\":";
    o += std::to_string(m.rss_kb);
    o += ",\"readable\":";
    o += m.readable ? "true" : "false";
    o += ",\"rel\":";
    o += JsonEsc(ChainRelName(m.rel));
    o += ",\"image\":";
    o += JsonW(m.image);
    o += ",\"cmdline\":";
    o += JsonW(m.cmdline.size() > 200 ? m.cmdline.substr(0, 200) : m.cmdline);
    o += "}";
  }
  o += "]";
  return o;
}

std::string DumpJobJson(const JobResult& job) {
  std::string o = "{\"name\":";
  o += JsonEsc(job.name);
  o += ",\"status\":";
  o += JsonEsc(job.status);
  o += ",\"source_pid\":";
  o += std::to_string(job.source_pid);
  o += ",\"stages\":[";
  for (std::size_t i = 0; i < job.stages.size(); ++i) {
    if (i) o.push_back(',');
    const auto& s = job.stages[i];
    o += "{\"id\":";
    o += JsonEsc(s.id);
    o += ",\"role\":";
    o += JsonEsc(s.role);
    o += ",\"pid\":";
    o += std::to_string(s.pid);
    o += ",\"image\":";
    o += JsonW(s.image);
    o += ",\"find\":";
    o += JsonW(s.find);
    o += ",\"status\":";
    o += JsonEsc(StageStatusName(s.status));
    o += ",\"attempts\":";
    o += std::to_string(s.attempts);
    o += ",\"stale\":";
    o += s.stale ? "true" : "false";
    o += ",\"detail\":";
    o += JsonEsc(s.detail);
    o += "}";
  }
  o += "]}";
  return o;
}

}  // namespace secdogie::atlas
