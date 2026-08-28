#include "mct_command.h"
#include "mct_server.h"

#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

#if !defined(_WIN32)
#include <unistd.h>
#endif

using namespace secdogie::atlas;

static void Usage() {
  std::fputs(
      "atlas_mct — 应用程式 (Windows EXE / Linux / macOS app)\n"
      "read-only hybrid UIA + process-memory inspector\n"
      "\n"
      "  Native application command plane (not a web script).\n"
      "  Binds 127.0.0.1 only. 0.0.0.0 / LAN / wildcard refused.\n"
      "  Port is operator-chosen (default 17890, 0 = ephemeral).\n"
      "  Commands: list · inspect <pid|name> · find <control> · chain · link · job report / 报表\n"
      "  Never writes the target. TI / PPL / VM_WRITE / ALL_ACCESS refused.\n"
      "\n"
      "  atlas_mct.exe --listen 127.0.0.1:17890\n"
      "  atlas_mct --listen 127.0.0.1:0 --repl\n"
      "  POST /cmd  {\"line\":\"inspect atlas_target\"}\n"
      "  GET  /health   GET /list   POST /inspect\n",
      stderr);
}

int main(int argc, char** argv) {
  MctSetArgv0(argc > 0 ? argv[0] : "atlas_mct");
  ListenSpec spec;
  bool repl = false;
  bool listen = true;
  std::string err;

  for (int i = 1; i < argc; ++i) {
    const char* a = argv[i];
    if (std::strcmp(a, "-h") == 0 || std::strcmp(a, "--help") == 0) {
      Usage();
      return 0;
    }
    if (std::strcmp(a, "--repl") == 0) {
      repl = true;
      continue;
    }
    if (std::strcmp(a, "--no-listen") == 0) {
      listen = false;
      repl = true;
      continue;
    }
    if (std::strcmp(a, "--listen") == 0 && i + 1 < argc) {
      if (!ParseListenSpec(argv[++i], &spec, &err)) {
        std::fprintf(stderr, "%s\n", err.c_str());
        return 2;
      }
      listen = true;
      continue;
    }
    if (std::strcmp(a, "--port") == 0 && i + 1 < argc) {
      if (!ParseListenSpec(argv[++i], &spec, &err)) {
        std::fprintf(stderr, "%s\n", err.c_str());
        return 2;
      }
      continue;
    }
    if (std::strcmp(a, "--cmd") == 0 && i + 1 < argc) {
      MctState st;
      const std::string out = ExecMctLine(st, argv[++i]);
      std::fwrite(out.data(), 1, out.size(), stdout);
      std::putchar('\n');
      return out.find("\"ok\":false") == std::string::npos ? 0 : 1;
    }
    std::fprintf(stderr, "unknown arg %s\n", a);
    Usage();
    return 2;
  }

  if (!HostIsLoopback(spec.host)) {
    std::fputs("refused: MCT binds 127.0.0.1 only\n", stderr);
    return 2;
  }

  MctEnsureFixture();

#if !defined(_WIN32)
  const bool tty = ::isatty(0) != 0;
#else
  const bool tty = true;
#endif
  if (repl || (tty && !listen)) {
    std::thread repl_thr([] {
      MctState st;
      std::string line;
      std::fputs("应用程式> ", stdout);
      std::fflush(stdout);
      while (std::getline(std::cin, line)) {
        const std::string out = ExecMctLine(st, line);
        std::fwrite(out.data(), 1, out.size(), stdout);
        std::fputs("\n应用程式> ", stdout);
        std::fflush(stdout);
      }
    });
    if (!listen) {
      repl_thr.join();
      return 0;
    }
    repl_thr.detach();
  }

  if (!listen) return 0;
  ListenSpec bound;
  const int rc = ServeMct(spec, &bound, &err);
  if (rc != 0) {
    std::fprintf(stderr, "atlas_mct: %s\n", err.c_str());
    return rc;
  }
  return 0;
}
