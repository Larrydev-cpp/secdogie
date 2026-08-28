#pragma once

// Operator command interpreter for atlas_mct (Windows EXE / POSIX binary).
// Parse + execute live on this process. Never writes the target.

#include "process_chain.h"

#include <cstdint>
#include <string>
#include <vector>

namespace secdogie::atlas {

struct MctState {
  std::uint32_t pid = 0;
  bool include_mapped = false;
  std::size_t max_mb = 8;
  std::wstring find;
  std::vector<std::uint32_t> linked;
  std::string last_snap;
  std::uint32_t last_pid = 0;
  std::vector<ChainMember> last_chain;
};

void MctSetArgv0(const char* argv0);
std::uint32_t MctEnsureFixture();

enum class MctOp {
  Help,
  List,
  Inspect,
  Find,
  Graphics,
  Status,
  Mapped,
  Clear,
  Chain,
  Link,
  Unlink,
  Job,
  Unknown
};

MctOp ParseMctLine(const std::string& line, std::string* arg);

// Full JSON object: ok/op/result plus snapshot, chain, or job.
std::string ExecMctLine(MctState& st, const std::string& line);

}  // namespace secdogie::atlas
