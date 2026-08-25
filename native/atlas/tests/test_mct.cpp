#include "mct_command.h"
#include "mct_server.h"

#include <cstdio>
#include <cstring>
#include <string>
#include <thread>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
using sock_t = SOCKET;
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
using sock_t = int;
#endif

using namespace secdogie::atlas;

extern int g_failed;
extern int g_passed;
void Expect(bool cond, const char* name, const char* detail);

static std::string HttpGet(std::uint16_t port, const char* path) {
#if defined(_WIN32)
  WSADATA w{};
  WSAStartup(MAKEWORD(2, 2), &w);
#endif
  sock_t fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (fd < 0 || fd == static_cast<sock_t>(-1)) return {};
#if defined(_WIN32)
  DWORD tv = 3000;
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&tv), sizeof(tv));
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&tv), sizeof(tv));
#else
  timeval tv{};
  tv.tv_sec = 3;
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
#endif
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_port = htons(port);
  a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (connect(fd, reinterpret_cast<sockaddr*>(&a), sizeof(a)) != 0) {
#if defined(_WIN32)
    closesocket(fd);
#else
    close(fd);
#endif
    return {};
  }
  char req[256];
  const int n = std::snprintf(req, sizeof(req),
                              "GET %s HTTP/1.1\r\nHost: 127.0.0.1:%u\r\nConnection: close\r\n\r\n",
                              path, port);
#if defined(_WIN32)
  send(fd, req, n, 0);
#else
  send(fd, req, static_cast<size_t>(n), 0);
#endif
  std::string out;
  char buf[2048];
  for (;;) {
#if defined(_WIN32)
    const int r = recv(fd, buf, sizeof(buf), 0);
#else
    const ssize_t r = recv(fd, buf, sizeof(buf), 0);
#endif
    if (r <= 0) break;
    out.append(buf, static_cast<std::size_t>(r));
  }
#if defined(_WIN32)
  closesocket(fd);
#else
  close(fd);
#endif
  return out;
}

void RunMctTests() {
  {
    Expect(HostIsLoopback("127.0.0.1"), "127.0.0.1 is loopback", "host");
    Expect(HostIsLoopback("localhost"), "localhost is loopback", "host");
    Expect(HostIsLoopback("::1"), "::1 is loopback", "host");
    Expect(!HostIsLoopback("0.0.0.0"), "0.0.0.0 is not loopback", "host");
    Expect(!HostIsLoopback("1.1.1.1"), "public IP is not loopback", "host");
  }
  {
    ListenSpec spec;
    std::string err;
    Expect(!ParseListenSpec("0.0.0.0:80", &spec, &err), "ParseListenSpec refuses 0.0.0.0",
           err.c_str());
    Expect(!ParseListenSpec("192.168.1.1:9", &spec, &err), "ParseListenSpec refuses LAN",
           err.c_str());
    Expect(ParseListenSpec("127.0.0.1:17890", &spec, &err) && spec.port == 17890,
           "ParseListenSpec accepts 127.0.0.1:17890", err.c_str());
    Expect(ParseListenSpec("localhost:0", &spec, &err) && spec.port == 0,
           "ParseListenSpec accepts localhost:0", err.c_str());
  }
  {
    std::string arg;
    Expect(ParseMctLine("list", &arg) == MctOp::List, "parse list", "op");
    Expect(ParseMctLine("inspect atlas_target", &arg) == MctOp::Inspect && arg == "atlas_target",
           "parse inspect atlas_target", arg.c_str());
    Expect(ParseMctLine("4050", &arg) == MctOp::Inspect && arg == "4050", "bare pid is inspect",
           arg.c_str());
    Expect(ParseMctLine("find Zoom Extents", &arg) == MctOp::Find && arg == "Zoom Extents",
           "parse find", arg.c_str());
    Expect(ParseMctLine("graphics", &arg) == MctOp::Graphics, "parse graphics", "op");
    Expect(ParseMctLine("图层尺寸", &arg) == MctOp::Find, "bare CJK is find", "op");
  }
  {
    MctState st;
    const std::string help = ExecMctLine(st, "help");
    Expect(help.find("\"ok\":true") != std::string::npos && help.find("inspect") != std::string::npos,
           "ExecMctLine help is JSON", help.c_str());
  }
  {
    ListenSpec spec;
    spec.port = 0;
    ListenSpec bound;
    std::string err;
    std::thread thr([&] { ServeMct(spec, &bound, &err); });
#if defined(_WIN32)
    Sleep(250);
#else
    usleep(250000);
#endif
    Expect(bound.port != 0, "BindLoopback ephemeral port assigned",
           err.empty() ? "port 0" : err.c_str());
    std::string health;
    if (bound.port) health = HttpGet(bound.port, "/health");
    Expect(health.find("atlas_mct") != std::string::npos && health.find("127.0.0.1") != std::string::npos,
           "GET /health on loopback", health.c_str());
    std::string listed;
    if (bound.port) listed = HttpGet(bound.port, "/list");
    Expect(listed.find("\"op\":\"list\"") != std::string::npos ||
               listed.find("\"processes\"") != std::string::npos,
           "GET /list through atlas_mct", listed.c_str());
    StopMct();
    if (thr.joinable()) thr.join();
  }
}
