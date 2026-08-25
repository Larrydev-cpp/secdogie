#pragma once

// Loopback-only command plane for atlas_mct.
// Binds 127.0.0.1 / ::1. 0.0.0.0 / LAN / wildcard refused.

#include <cstdint>
#include <string>

namespace secdogie::atlas {

struct ListenSpec {
  std::string host = "127.0.0.1";
  std::uint16_t port = 17890;
};

bool HostIsLoopback(const std::string& host) noexcept;
bool ParseListenSpec(const char* spec, ListenSpec* out, std::string* err);

// Bind 127.0.0.1:port (port 0 → ephemeral). Returns listen fd or -1.
int BindLoopback(const ListenSpec& spec, ListenSpec* bound, std::string* err);

// Blocking accept loop. Writes bound host:port to *bound and to the port file.
int ServeMct(const ListenSpec& spec, ListenSpec* bound, std::string* err);

void StopMct();
std::string MctPortFilePath();
void WriteMctPortFile(const ListenSpec& bound);

}  // namespace secdogie::atlas
