#pragma once

// JSON dump for atlas_inspect / atlas_mct. Always UTF-8. Never '?'.

#include "memory_inspector.h"

#include <cstdint>
#include <string>

namespace secdogie::atlas {

const char* PlatformName() noexcept;

std::string DumpListJson();
std::string DumpInspectJson(std::uint32_t pid, const InspectConfig& cfg,
                            const std::wstring& find_name);

}  // namespace secdogie::atlas
