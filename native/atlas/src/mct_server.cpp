#include "mct_server.h"

#include "inspect_json.h"
#include "mct_command.h"
#include "utf.h"

#include <atomic>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <mutex>
#include <string>
#include <vector>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
using sock_t = SOCKET;
static void CloseSock(sock_t s) { closesocket(s); }
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
using sock_t = int;
static const int INVALID_SOCKET = -1;
static void CloseSock(sock_t s) { ::close(s); }
#endif

namespace secdogie::atlas {
namespace {

std::mutex g_exec;
MctState g_state;
std::atomic<sock_t> g_listen{INVALID_SOCKET};
std::atomic<std::uint16_t> g_port{0};
std::atomic<bool> g_stop{false};

#if defined(_WIN32)
struct WinsockOnce {
  WinsockOnce() {
    WSADATA w{};
    WSAStartup(MAKEWORD(2, 2), &w);
  }
};
void EnsureWinsock() {
  static WinsockOnce once;
  (void)once;
}
#else
void EnsureWinsock() {}
#endif

std::string Lower(std::string s) {
  for (char& c : s) {
    if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);
  }
  return s;
}

bool RecvAll(sock_t fd, std::string* out, std::size_t cap) {
  char buf[4096];
  while (out->size() < cap) {
#if defined(_WIN32)
    const int n = recv(fd, buf, sizeof(buf), 0);
#else
    const ssize_t n = ::recv(fd, buf, sizeof(buf), 0);
#endif
    if (n == 0) return !out->empty();
    if (n < 0) return false;
    out->append(buf, static_cast<std::size_t>(n));
    if (out->find("\r\n\r\n") != std::string::npos) {
      const auto hdr_end = out->find("\r\n\r\n");
      std::size_t need = hdr_end + 4;
      auto cl = Lower(*out).find("content-length:");
      if (cl != std::string::npos && cl < hdr_end) {
        unsigned long len = 0;
        std::sscanf(out->c_str() + cl + 15, "%lu", &len);
        if (len > 65536) len = 65536;
        need += static_cast<std::size_t>(len);
      }
      if (out->size() >= need) {
        out->resize(need);
        return true;
      }
    }
    // line protocol (no HTTP): one line
    if (out->find('\n') != std::string::npos && out->find("HTTP/") == std::string::npos &&
        out->find("GET ") != 0 && out->find("POST ") != 0 && out->find("OPTIONS ") != 0) {
      return true;
    }
  }
  return true;
}

void SendAll(sock_t fd, const char* p, std::size_t n) {
  std::size_t off = 0;
  while (off < n) {
#if defined(_WIN32)
    const int w = send(fd, p + off, static_cast<int>(n - off), 0);
#else
    const ssize_t w = ::send(fd, p + off, n - off, 0);
#endif
    if (w <= 0) return;
    off += static_cast<std::size_t>(w);
  }
}

void HttpReply(sock_t fd, int code, const std::string& body) {
  const char* st = code == 200 ? "OK" : (code == 400 ? "Bad Request" : "Error");
  char hdr[256];
  const int n = std::snprintf(hdr, sizeof(hdr),
                              "HTTP/1.1 %d %s\r\n"
                              "Content-Type: application/json; charset=utf-8\r\n"
                              "Content-Length: %zu\r\n"
                              "Connection: close\r\n"
                              "Cache-Control: no-store\r\n"
                              "\r\n",
                              code, st, body.size());
  SendAll(fd, hdr, static_cast<std::size_t>(n));
  SendAll(fd, body.data(), body.size());
}

std::string HeaderValue(const std::string& req, const char* name) {
  const std::string lower = Lower(req);
  const std::string key = Lower(name) + ":";
  auto p = lower.find(key);
  if (p == std::string::npos) return {};
  p += key.size();
  while (p < lower.size() && (req[p] == ' ' || req[p] == '\t')) ++p;
  auto e = req.find("\r\n", p);
  if (e == std::string::npos) e = req.size();
  return req.substr(p, e - p);
}

std::string JsonLineField(const std::string& body) {
  auto p = body.find("\"line\"");
  if (p == std::string::npos) return body;
  p = body.find(':', p);
  if (p == std::string::npos) return {};
  p = body.find('"', p);
  if (p == std::string::npos) return {};
  ++p;
  std::string o;
  for (; p < body.size(); ++p) {
    if (body[p] == '\\' && p + 1 < body.size()) {
      o.push_back(body[p + 1]);
      ++p;
      continue;
    }
    if (body[p] == '"') break;
    o.push_back(body[p]);
  }
  return o;
}

void HandleConn(sock_t fd) {
#if defined(_WIN32)
  DWORD tv = 5000;
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&tv), sizeof(tv));
#else
  timeval tv{};
  tv.tv_sec = 5;
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
#endif
  std::string req;
  if (!RecvAll(fd, &req, 256 * 1024)) {
    CloseSock(fd);
    return;
  }
  // Line protocol
  if (req.compare(0, 4, "GET ") != 0 && req.compare(0, 5, "POST ") != 0 &&
      req.compare(0, 8, "OPTIONS ") != 0) {
    std::string line = req;
    while (!line.empty() && (line.back() == '\n' || line.back() == '\r')) line.pop_back();
    std::string out;
    {
      std::lock_guard<std::mutex> lock(g_exec);
      out = ExecMctLine(g_state, line);
    }
    out.push_back('\n');
    SendAll(fd, out.data(), out.size());
    CloseSock(fd);
    return;
  }

  const std::string host = HeaderValue(req, "Host");
  if (!host.empty()) {
    const auto colon = host.find(':');
    const std::string h = colon == std::string::npos ? host : host.substr(0, colon);
    if (!HostIsLoopback(h)) {
      HttpReply(fd, 403, "{\"ok\":false,\"detail\":\"host is not loopback\"}");
      CloseSock(fd);
      return;
    }
  }

  std::string method;
  std::string path;
  {
    auto sp1 = req.find(' ');
    if (sp1 == std::string::npos) {
      CloseSock(fd);
      return;
    }
    method = req.substr(0, sp1);
    auto sp2 = req.find(' ', sp1 + 1);
    path = req.substr(sp1 + 1, sp2 - sp1 - 1);
    auto q = path.find('?');
    if (q != std::string::npos) path.resize(q);
  }

  if (method == "OPTIONS") {
    HttpReply(fd, 200, "{\"ok\":true}");
    CloseSock(fd);
    return;
  }

  const auto hdr_end = req.find("\r\n\r\n");
  std::string body;
  if (hdr_end != std::string::npos) body = req.substr(hdr_end + 4);

  if (method == "GET" && (path == "/health" || path == "/status")) {
    char b[320];
    std::snprintf(b, sizeof(b),
                  "{\"ok\":true,\"app\":\"atlas_mct\",\"kind\":\"exe\",\"bind\":\"127.0.0.1\","
                  "\"platform\":\"%s\",\"readonly\":true,\"pid\":%u}",
                  PlatformName(), g_state.pid);
    HttpReply(fd, 200, b);
    CloseSock(fd);
    return;
  }

  std::string out;
  {
    std::lock_guard<std::mutex> lock(g_exec);
    if (method == "GET" && path == "/list") {
      out = ExecMctLine(g_state, "list");
    } else if (method == "POST" && path == "/cmd") {
      out = ExecMctLine(g_state, JsonLineField(body));
    } else if (method == "POST" && path == "/inspect") {
      std::string line = "inspect ";
      auto pidp = body.find("\"pid\"");
      if (pidp != std::string::npos) {
        unsigned long pid = 0;
        std::sscanf(body.c_str() + pidp, "\"pid\"%*[^0-9]%lu", &pid);
        if (pid > 0) line = "inspect " + std::to_string(pid);
      }
      auto findp = body.find("\"find\"");
      if (findp != std::string::npos) {
        const std::string f = JsonLineField(std::string("{\"line\":") + body.substr(findp + 6) + "}");
        (void)f;
      }
      out = ExecMctLine(g_state, line);
      if (body.find("\"find\"") != std::string::npos) {
        auto q = body.find('"', body.find(':', body.find("\"find\"")));
        if (q != std::string::npos) {
          std::string name;
          for (auto i = q + 1; i < body.size() && body[i] != '"'; ++i) name.push_back(body[i]);
          if (!name.empty()) out = ExecMctLine(g_state, "find " + name);
        }
      }
    } else if (method == "GET" && path == "/cmd") {
      out = "{\"ok\":false,\"detail\":\"use POST /cmd\"}";
    } else {
      out = "{\"ok\":false,\"detail\":\"unknown path\"}";
    }
  }
  HttpReply(fd, 200, out);
  CloseSock(fd);
}

}  // namespace

bool HostIsLoopback(const std::string& host) noexcept {
  if (host.empty()) return true;
  const std::string h = Lower(host);
  return h == "127.0.0.1" || h == "localhost" || h == "::1" || h == "[::1]" || h == "127.0.0.1.";
}

bool ParseListenSpec(const char* spec, ListenSpec* out, std::string* err) {
  if (!out) return false;
  *out = ListenSpec{};
  if (!spec || !*spec) return true;
  std::string s = spec;
  if (s.rfind("http://", 0) == 0) s = s.substr(7);
  if (s.rfind("https://", 0) == 0) s = s.substr(8);
  std::string host = "127.0.0.1";
  std::string port_s;
  if (!s.empty() && s[0] == ':') {
    port_s = s.substr(1);
  } else {
    const auto colon = s.rfind(':');
    if (colon == std::string::npos) {
      bool digits = true;
      for (char c : s) {
        if (c < '0' || c > '9') digits = false;
      }
      if (digits) port_s = s;
      else host = s;
    } else {
      host = s.substr(0, colon);
      port_s = s.substr(colon + 1);
    }
  }
  if (host.size() >= 2 && host.front() == '[' && host.back() == ']') {
    host = host.substr(1, host.size() - 2);
  }
  if (!HostIsLoopback(host)) {
    if (err) *err = "refused: MCT binds loopback only (127.0.0.1 / localhost / ::1), not " + host;
    return false;
  }
  out->host = host.empty() ? "127.0.0.1" : (host == "localhost" || host == "::1" ? "127.0.0.1" : host);
  if (!port_s.empty()) {
    const unsigned long p = std::strtoul(port_s.c_str(), nullptr, 10);
    if (p > 65535) {
      if (err) *err = "port out of range";
      return false;
    }
    out->port = static_cast<std::uint16_t>(p);
  }
  return true;
}

int BindLoopback(const ListenSpec& spec, ListenSpec* bound, std::string* err) {
  EnsureWinsock();
  if (!HostIsLoopback(spec.host)) {
    if (err) *err = "refused: not loopback";
    return -1;
  }
  sock_t fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (fd == INVALID_SOCKET) {
    if (err) *err = "socket failed";
    return -1;
  }
  int on = 1;
  setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&on), sizeof(on));
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(spec.port);
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
    if (err) *err = "bind 127.0.0.1 failed";
    CloseSock(fd);
    return -1;
  }
  if (listen(fd, 16) != 0) {
    if (err) *err = "listen failed";
    CloseSock(fd);
    return -1;
  }
  sockaddr_in got{};
#if defined(_WIN32)
  int glen = sizeof(got);
#else
  socklen_t glen = sizeof(got);
#endif
  if (getsockname(fd, reinterpret_cast<sockaddr*>(&got), &glen) == 0 && bound) {
    bound->host = "127.0.0.1";
    bound->port = ntohs(got.sin_port);
  } else if (bound) {
    *bound = spec;
    if (bound->host != "127.0.0.1") bound->host = "127.0.0.1";
  }
  return static_cast<int>(fd);
}

std::string MctPortFilePath() {
#if defined(_WIN32)
  char tmp[MAX_PATH];
  const DWORD n = GetTempPathA(MAX_PATH, tmp);
  std::string p = n ? std::string(tmp, n) : std::string(".");
  if (!p.empty() && p.back() != '\\') p.push_back('\\');
  return p + "atlas_mct.port";
#else
  return "/tmp/atlas_mct.port";
#endif
}

void WriteMctPortFile(const ListenSpec& bound) {
  std::ofstream out(MctPortFilePath(), std::ios::trunc);
  if (!out) return;
  out << bound.host << ":" << bound.port << "\n";
}

void StopMct() {
  g_stop.store(true);
  const std::uint16_t port = g_port.load();
  sock_t fd = g_listen.exchange(INVALID_SOCKET);
  if (port != 0) {
    EnsureWinsock();
    sock_t w = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (w != INVALID_SOCKET) {
      sockaddr_in a{};
      a.sin_family = AF_INET;
      a.sin_port = htons(port);
      a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
      connect(w, reinterpret_cast<sockaddr*>(&a), sizeof(a));
      CloseSock(w);
    }
  }
  if (fd != INVALID_SOCKET) {
#if defined(_WIN32)
    shutdown(fd, SD_BOTH);
#else
    shutdown(fd, SHUT_RDWR);
#endif
    CloseSock(fd);
  }
}

int ServeMct(const ListenSpec& spec, ListenSpec* bound, std::string* err) {
  ListenSpec got = spec;
  const int fd = BindLoopback(spec, &got, err);
  if (fd < 0) return 1;
  g_listen.store(static_cast<sock_t>(fd));
  g_port.store(got.port);
  g_stop.store(false);
  if (bound) *bound = got;
  WriteMctPortFile(got);
  std::fprintf(stderr, "atlas_mct listening on 127.0.0.1:%u (loopback only)\n", got.port);
  std::fflush(stderr);
  while (!g_stop.load()) {
    sockaddr_in peer{};
#if defined(_WIN32)
    int plen = sizeof(peer);
    sock_t c = accept(static_cast<sock_t>(fd), reinterpret_cast<sockaddr*>(&peer), &plen);
#else
    socklen_t plen = sizeof(peer);
    sock_t c = ::accept(static_cast<sock_t>(fd), reinterpret_cast<sockaddr*>(&peer), &plen);
#endif
    if (c == INVALID_SOCKET) {
      if (g_stop.load()) break;
      continue;
    }
    if (g_stop.load()) {
      CloseSock(c);
      break;
    }
    const std::uint32_t ip = ntohl(peer.sin_addr.s_addr);
    if (ip != 0x7F000001u) {
      CloseSock(c);
      continue;
    }
    HandleConn(c);
  }
  StopMct();
  return 0;
}

}  // namespace secdogie::atlas
