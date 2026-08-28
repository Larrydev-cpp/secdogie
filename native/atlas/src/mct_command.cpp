#include "mct_command.h"

#include "inspect_json.h"
#include "process_perception.h"
#include "utf.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace secdogie::atlas {
namespace {

std::string g_argv0;

std::string JsonEscapeValue(const std::string& s) {
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

std::string Lower(std::string s) {
  for (char& c : s) {
    if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);
  }
  return s;
}

std::string BasenameUtf8(const std::wstring& path) {
  const std::string s = WideToUtf8(path);
  const auto slash = s.find_last_of("/\\");
  return slash == std::string::npos ? s : s.substr(slash + 1);
}

std::string DirnameOf(const std::string& path) {
  const auto slash = path.find_last_of("/\\");
  if (slash == std::string::npos) return ".";
  if (slash == 0) return path.substr(0, 1);
  return path.substr(0, slash);
}

bool FileExists(const std::string& p) {
#if defined(_WIN32)
  const DWORD a = GetFileAttributesA(p.c_str());
  return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY) == 0;
#else
  struct stat st {};
  return ::stat(p.c_str(), &st) == 0 && S_ISREG(st.st_mode);
#endif
}

std::string TargetPath() {
#if defined(_WIN32)
  const char* name = "atlas_target.exe";
#else
  const char* name = "atlas_target";
#endif
  std::vector<std::string> cands;
  if (!g_argv0.empty()) cands.push_back(DirnameOf(g_argv0) + "/" + name);
  cands.push_back(std::string("./") + name);
  cands.push_back(std::string("/workspace/bin/") + name);
  for (const auto& p : cands) {
    if (FileExists(p)) return p;
  }
  return {};
}

int ScoreProcess(const ListedProcess& p, const std::string& q) {
  if (q.empty()) return 0;
  if (std::to_string(p.pid) == q) return p.readable ? 10000 : 1;
  int s = 0;
  const std::string image = Lower(WideToUtf8(p.image));
  const std::string base = Lower(BasenameUtf8(p.image));
  const std::string cmd = Lower(WideToUtf8(p.cmdline));
  const std::string ql = Lower(q);
  if (base == ql || image == ql) s += 160;
  else if (base.find(ql) != std::string::npos || image.find(ql) != std::string::npos) s += 50;
  if (cmd.find(ql) != std::string::npos) s += 15;
  if (base.find("atlas_target") != std::string::npos) s += 40;
  if (!p.readable) s -= 200;
  else s += 30;
  if (base.find("atlas_inspect") != std::string::npos && ql.find("atlas") != std::string::npos &&
      ql.find("target") == std::string::npos) {
    s -= 80;
  }
  if (base.find("atlas_mct") != std::string::npos && ql.find("atlas") != std::string::npos &&
      ql.find("target") == std::string::npos) {
    s -= 80;
  }
  if (p.rss_kb == 0) s -= 40;
  return s;
}

const ListedProcess* Resolve(const std::vector<ListedProcess>& procs, const std::string& query) {
  const std::string q = query;
  if (q.empty()) {
    const ListedProcess* best = nullptr;
    for (const auto& p : procs) {
      if (!p.readable) continue;
      const std::string base = Lower(BasenameUtf8(p.image));
      if (base.find("atlas_target") != std::string::npos && p.rss_kb > 0) return &p;
      if (base.find("atlas_target") != std::string::npos) best = &p;
    }
    if (best) return best;
    for (const auto& p : procs) {
      if (p.readable) return &p;
    }
    return nullptr;
  }
  bool digits = !q.empty();
  for (char c : q) {
    if (c < '0' || c > '9') {
      digits = false;
      break;
    }
  }
  if (digits) {
    const unsigned long pid = std::strtoul(q.c_str(), nullptr, 10);
    for (const auto& p : procs) {
      if (p.pid == static_cast<std::uint32_t>(pid)) return &p;
    }
    return nullptr;
  }
  const ListedProcess* best = nullptr;
  int best_s = 0;
  for (const auto& p : procs) {
    if (!p.readable) continue;
    const int s = ScoreProcess(p, q);
    if (s > best_s) {
      best = &p;
      best_s = s;
    }
  }
  return best_s >= 25 ? best : nullptr;
}

std::string Wrap(bool ok, const std::string& op, const std::string& result,
                 const std::string& extra) {
  std::string o = "{\"ok\":";
  o += ok ? "true" : "false";
  o += ",\"app\":\"atlas_mct\",\"bind\":\"127.0.0.1\",\"op\":";
  o += JsonEscapeValue(op);
  o += ",\"result\":";
  o += JsonEscapeValue(result);
  if (!extra.empty()) {
    o += ",";
    o += extra;
  }
  o += "}";
  return o;
}

}  // namespace

void MctSetArgv0(const char* argv0) {
  g_argv0 = argv0 ? argv0 : "";
}

std::uint32_t MctEnsureFixture() {
  const std::vector<ListedProcess> procs = ProcessPerception::ListProcesses();
  for (const auto& p : procs) {
    if (!p.readable) continue;
    if (Lower(BasenameUtf8(p.image)).find("atlas_target") != std::string::npos && p.rss_kb > 0) {
      return p.pid;
    }
  }
  const std::string path = TargetPath();
  if (path.empty()) return 0;
#if defined(_WIN32)
  STARTUPINFOA si{};
  si.cb = sizeof(si);
  PROCESS_INFORMATION pi{};
  std::string cmd = "\"" + path + "\"";
  std::vector<char> buf(cmd.begin(), cmd.end());
  buf.push_back(0);
  if (!CreateProcessA(path.c_str(), buf.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
                      nullptr, nullptr, &si, &pi)) {
    return 0;
  }
  const std::uint32_t pid = pi.dwProcessId;
  CloseHandle(pi.hThread);
  CloseHandle(pi.hProcess);
  Sleep(150);
  return pid;
#else
  const pid_t child = fork();
  if (child == 0) {
    const int n = ::open("/dev/null", O_RDWR);
    if (n >= 0) {
      dup2(n, 0);
      dup2(n, 1);
      if (n > 2) close(n);
    }
    execl(path.c_str(), path.c_str(), static_cast<char*>(nullptr));
    _exit(127);
  }
  if (child < 0) return 0;
  usleep(150000);
  return static_cast<std::uint32_t>(child);
#endif
}

MctOp ParseMctLine(const std::string& line, std::string* arg) {
  std::string t = line;
  while (!t.empty() && (t.back() == '\r' || t.back() == ' ' || t.back() == '\t')) t.pop_back();
  std::size_t i = 0;
  while (i < t.size() && (t[i] == ' ' || t[i] == '\t')) ++i;
  t = t.substr(i);
  if (arg) arg->clear();
  if (t.empty()) return MctOp::Help;
  std::string head;
  std::string rest;
  const auto sp = t.find(' ');
  if (sp == std::string::npos) head = t;
  else {
    head = t.substr(0, sp);
    rest = t.substr(sp + 1);
    while (!rest.empty() && rest[0] == ' ') rest.erase(rest.begin());
  }
  const std::string h = Lower(head);
  if (arg) *arg = rest;
  if (h == "help" || h == "?" || h == "h" || head == "帮助") return MctOp::Help;
  if (h == "list" || h == "ls" || h == "ps" || head == "列表" || head == "进程") return MctOp::List;
  if (h == "clear" || h == "clr" || head == "清除") return MctOp::Clear;
  if (h == "graphics" || h == "gfx" || head == "图形" || h == "dib" || h == "viewport") {
    return MctOp::Graphics;
  }
  if (h == "status" || h == "health") return MctOp::Status;
  if (h == "mapped" || head == "映射") return MctOp::Mapped;
  if (h == "inspect" || h == "open" || h == "pid" || head == "检查" || head == "打开") {
    if (arg && arg->empty()) *arg = "atlas_target";
    return MctOp::Inspect;
  }
  if (h == "find" || head == "查找" || head == "找" || h == "lock") return MctOp::Find;
  if (h == "zoom" || head == "缩放" || h == "extents") {
    if (arg) *arg = "Zoom Extents";
    return MctOp::Find;
  }
  if (h == "layer" || head == "图层" || h == "dims" || h == "layer_dims") {
    if (arg) {
      *arg = rest.find("尺") != std::string::npos || rest.find("cjk") != std::string::npos
                 ? "图层尺寸"
                 : "LAYER_DIMS";
    }
    return MctOp::Find;
  }
  bool digits = !t.empty();
  for (char c : t) {
    if (c < '0' || c > '9') {
      digits = false;
      break;
    }
  }
  if (digits) {
    if (arg) *arg = t;
    return MctOp::Inspect;
  }
  if ((t.find("atlas_target") != std::string::npos || t.find("acad.exe") != std::string::npos) &&
      t.find(' ') == std::string::npos) {
    if (arg) *arg = t;
    return MctOp::Inspect;
  }
  if (arg) *arg = t;
  return MctOp::Find;
}

std::string ExecMctLine(MctState& st, const std::string& line) {
  std::string arg;
  const MctOp op = ParseMctLine(line, &arg);
  InspectConfig cfg;
  cfg.max_bytes = st.max_mb * 1024ull * 1024ull;
  cfg.include_mapped = st.include_mapped;
  cfg.max_strings = 400;

  switch (op) {
    case MctOp::Help:
      return Wrap(true, "help",
                  "list · inspect <pid|name> · find <control> · zoom · layer · 图层尺寸 · "
                  "graphics · mapped on|off · status · clear",
                  "\"kind\":\"app\"");
    case MctOp::Status:
      return Wrap(true, "status", "atlas_mct 应用程式 loopback, read-only",
                  std::string("\"platform\":\"") + PlatformName() + "\",\"kind\":\"app\",\"pid\":" +
                      std::to_string(st.pid));
    case MctOp::Clear:
      st.pid = 0;
      st.find.clear();
      return Wrap(true, "clear", "cleared", "");
    case MctOp::Mapped:
      st.include_mapped = arg != "off" && arg != "0" && arg != "关";
      return Wrap(true, "mapped", st.include_mapped ? "mapped pages on" : "private pages only", "");
    case MctOp::List: {
      MctEnsureFixture();
      const std::string list = DumpListJson();
      return Wrap(true, "list", "process list", std::string("\"list\":") + list);
    }
    case MctOp::Inspect: {
      MctEnsureFixture();
      const auto procs = ProcessPerception::ListProcesses();
      const ListedProcess* p = Resolve(procs, arg);
      if (!p) {
        return Wrap(false, "inspect", "no matching process: " + arg, "");
      }
      if (!p->readable) {
        return Wrap(false, "inspect",
                    "pid " + std::to_string(p->pid) + " unreadable (maps/dumpable)", "");
      }
      st.pid = p->pid;
      const std::string snap = DumpInspectJson(p->pid, cfg, st.find);
      const std::string image = BasenameUtf8(p->image);
      return Wrap(true, "inspect", "pid " + std::to_string(p->pid) + " " + image,
                  std::string("\"pid\":") + std::to_string(p->pid) + ",\"snapshot\":" + snap);
    }
    case MctOp::Find: {
      if (arg.empty()) return Wrap(false, "find", "usage: find <name>", "");
      st.find = Utf8ToWide(arg);
      if (st.pid == 0) {
        MctEnsureFixture();
        const auto procs = ProcessPerception::ListProcesses();
        const ListedProcess* p = Resolve(procs, "");
        if (p) st.pid = p->pid;
      }
      if (st.pid == 0) return Wrap(false, "find", "no readable target", "");
      const std::string snap = DumpInspectJson(st.pid, cfg, st.find);
      const bool hit = snap.find("\"found\":null") == std::string::npos &&
                       snap.find("\"found\":") != std::string::npos;
      return Wrap(hit, "find", hit ? ("HIT " + arg) : ("MISS " + arg),
                  std::string("\"pid\":") + std::to_string(st.pid) + ",\"snapshot\":" + snap);
    }
    case MctOp::Graphics: {
      if (st.pid == 0) {
        MctEnsureFixture();
        const auto procs = ProcessPerception::ListProcesses();
        const ListedProcess* p = Resolve(procs, "");
        if (p) st.pid = p->pid;
      }
      if (st.pid == 0) return Wrap(false, "graphics", "no readable target", "");
      const std::string snap = DumpInspectJson(st.pid, cfg, L"");
      return Wrap(true, "graphics", "viewport from process memory",
                  std::string("\"pid\":") + std::to_string(st.pid) + ",\"tab\":\"graphics\",\"snapshot\":" +
                      snap);
    }
    default:
      return Wrap(false, "unknown", "unknown command. help", "");
  }
}

}  // namespace secdogie::atlas
