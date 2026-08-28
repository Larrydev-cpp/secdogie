#pragma once

// Related-process graph + isolated job runner.
// One unread PID or a jittered inspect must not abort the whole job.
// Never writes the target.

#include "memory_inspector.h"
#include "process_perception.h"

#include <cstdint>
#include <string>
#include <vector>

namespace secdogie::atlas {

enum class ChainRel { Self, Parent, Child, Sibling, Session, Family, Linked };

const char* ChainRelName(ChainRel r) noexcept;

struct ChainMember {
  std::uint32_t pid = 0;
  std::uint32_t ppid = 0;
  std::uint32_t session_id = 0;
  std::wstring image;
  std::wstring cmdline;
  bool readable = false;
  std::uint64_t rss_kb = 0;
  ChainRel rel = ChainRel::Self;
};

// Known families only (atlas / acad / office / browser). Empty = no family match.
std::string ProcessFamily(const ListedProcess& p);

std::vector<ChainMember> BuildChain(std::uint32_t root_pid,
                                    const std::vector<ListedProcess>& all,
                                    const std::vector<std::uint32_t>& linked);

// Re-attach members that vanished (process exit / tunnel drop) so the job can
// isolate them instead of silently dropping the related PID.
void MergeLastKnown(std::vector<ChainMember>& live, const std::vector<ChainMember>& last);

enum class StageStatus { Pending, Running, Passed, Isolated, Failed, Skipped };

const char* StageStatusName(StageStatus s) noexcept;

struct JobStage {
  std::string id;
  std::string role;
  std::uint32_t pid = 0;
  std::wstring image;
  std::wstring find;
  StageStatus status = StageStatus::Pending;
  int attempts = 0;
  bool stale = false;
  std::string detail;
};

struct JobResult {
  std::string name;
  std::string status;  // passed | isolated | failed
  std::vector<JobStage> stages;
  std::uint32_t source_pid = 0;
  std::string last_snap;
};

struct InspectAttempt {
  bool ok = false;
  bool stale = false;
  int attempts = 0;
  std::string json;
  std::string detail;
};

void SleepMs(int ms);

// Transient inspect / process-gone: retry then fall back to last-known JSON.
InspectAttempt InspectWithRetry(std::uint32_t pid, const InspectConfig& cfg,
                                const std::wstring& find, const std::string* last_known,
                                int retries = 3);

JobResult RunReportJob(std::uint32_t source_pid, const std::vector<std::uint32_t>& linked,
                       const InspectConfig& cfg, const std::string* last_known,
                       const std::vector<ChainMember>* last_chain = nullptr);

std::string DumpChainJson(const std::vector<ChainMember>& chain);
std::string DumpJobJson(const JobResult& job);

}  // namespace secdogie::atlas
