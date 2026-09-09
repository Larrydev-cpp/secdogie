#pragma once

// JSON dump for atlas_inspect / atlas_mct. Always UTF-8. Never '?'.

#include "memory_inspector.h"
#include "process_perception.h"

#include <cstdint>
#include <string>

namespace secdogie::atlas {

const char* PlatformName() noexcept;

std::string DumpListJson();
std::string DumpInspectJson(std::uint32_t pid, const InspectConfig& cfg,
                            const std::wstring& find_name);

// macOS: capture the target window via CGWindow (Screen Recording) and
// prepend a dib with source=cgwindow. AX/UIA trees have no pixels. No-op
// off Darwin. Never HID.
void AttachWindowGraphics(InspectSnapshot& s, std::uint32_t pid,
                          const PerceptionSnapshot& uia);

}  // namespace secdogie::atlas
