#include "hybrid_tree.h"

#include <cstdio>
#include <string>


namespace secdogie::atlas {
namespace {

bool Ieq(const std::wstring& a, const std::wstring& b) {
  if (a.size() != b.size()) return false;
  for (std::size_t i = 0; i < a.size(); ++i) {
    wchar_t ca = a[i] >= L'A' && a[i] <= L'Z' ? static_cast<wchar_t>(a[i] + 32) : a[i];
    wchar_t cb = b[i] >= L'A' && b[i] <= L'Z' ? static_cast<wchar_t>(b[i] + 32) : b[i];
    if (ca != cb) return false;
  }
  return true;
}

HybridNode FromUia(const ControlNode& n) {
  HybridNode h;
  h.source = HybridSource::Uia;
  h.id = n.id;
  h.role = n.role;
  h.name = n.name;
  h.automation_id = n.automation_id;
  h.bounds = n.bounds;
  h.pid = n.pid;
  h.hwnd = n.hwnd;
  h.enabled = n.enabled;
  h.children.reserve(n.children.size());
  for (const auto& c : n.children) h.children.push_back(FromUia(c));
  return h;
}

void FlattenHybrid(std::vector<HybridNode>& nodes, std::vector<HybridNode*>& out) {
  for (auto& n : nodes) {
    out.push_back(&n);
    FlattenHybrid(n.children, out);
  }
}

}  // namespace

ControlNode MemoryHitAsControl(const MemoryHit& hit) {
  ControlNode n;
  char buf[48];
  std::snprintf(buf, sizeof(buf), "mem:%llx", static_cast<unsigned long long>(hit.address));
  n.id = buf;
  n.role = ControlRole::Text;
  n.name = hit.text;
  n.pid = hit.pid;
  n.enabled = true;
  return n;
}

std::vector<HybridNode> FuseTree(const std::vector<ControlNode>& uia,
                                 const std::vector<MemoryHit>& hits,
                                 std::size_t max_memory_only) {
  std::vector<HybridNode> roots;
  roots.reserve(uia.size());
  for (const auto& n : uia) roots.push_back(FromUia(n));

  std::vector<HybridNode*> flat;
  FlattenHybrid(roots, flat);

  std::vector<char> used(hits.size(), 0);
  for (HybridNode* node : flat) {
    for (std::size_t i = 0; i < hits.size(); ++i) {
      if (used[i]) continue;
      const MemoryHit& hit = hits[i];
      if ((!node->name.empty() && Ieq(node->name, hit.text)) ||
          (!node->automation_id.empty() && Ieq(node->automation_id, hit.text))) {
        node->source = HybridSource::Fused;
        node->address = hit.address;
        used[i] = 1;
        break;
      }
    }
  }

  std::size_t extra = 0;
  for (std::size_t i = 0; i < hits.size() && extra < max_memory_only; ++i) {
    if (used[i]) continue;
    HybridNode m;
    m.source = HybridSource::Memory;
    const ControlNode c = MemoryHitAsControl(hits[i]);
    m.id = c.id;
    m.role = c.role;
    m.name = c.name;
    m.pid = c.pid;
    m.address = hits[i].address;
    m.enabled = true;
    roots.push_back(std::move(m));
    ++extra;
  }
  return roots;
}

std::vector<ControlNode> HybridAsControls(const std::vector<HybridNode>& nodes) {
  std::vector<ControlNode> out;
  out.reserve(nodes.size());
  for (const auto& h : nodes) {
    ControlNode n;
    n.id = h.id;
    n.role = h.role;
    n.name = h.name;
    n.automation_id = h.automation_id;
    n.bounds = h.bounds;
    n.pid = h.pid;
    n.hwnd = h.hwnd;
    n.enabled = h.enabled;
    n.children = HybridAsControls(h.children);
    out.push_back(std::move(n));
  }
  return out;
}

}  // namespace secdogie::atlas
