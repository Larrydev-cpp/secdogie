#pragma once

// Unified Hybrid Tree Node: UIA control + memory hit fused by identity.
//
// When UIA produced a node whose name / AutomationId equals a string found
// in the process heap, the node is Source::Fused and carries the remote
// address. Memory-only hits (UIA miss, owner-drawn chrome) become
// Source::Memory nodes so HybridControlLoop::Find can still resolve a
// selector by name.

#include "memory_inspector.h"
#include "process_perception.h"

#include <cstdint>
#include <string>
#include <vector>

namespace secdogie::atlas {

enum class HybridSource { Uia, Memory, Fused };

struct HybridNode {
  HybridSource source = HybridSource::Uia;
  std::string id;
  ControlRole role = ControlRole::Custom;
  std::wstring name;
  std::wstring automation_id;
  Rect bounds;
  std::uint32_t pid = 0;
  std::uint64_t hwnd = 0;
  std::uint64_t address = 0;
  bool enabled = true;
  std::vector<HybridNode> children;
};

inline const char* HybridSourceName(HybridSource s) noexcept {
  switch (s) {
    case HybridSource::Uia: return "uia";
    case HybridSource::Memory: return "memory";
    case HybridSource::Fused: return "fused";
  }
  return "uia";
}

std::vector<HybridNode> FuseTree(const std::vector<ControlNode>& uia,
                                 const std::vector<MemoryHit>& hits,
                                 std::size_t max_memory_only = 1024);

std::vector<ControlNode> HybridAsControls(const std::vector<HybridNode>& nodes);

ControlNode MemoryHitAsControl(const MemoryHit& hit);

}  // namespace secdogie::atlas
