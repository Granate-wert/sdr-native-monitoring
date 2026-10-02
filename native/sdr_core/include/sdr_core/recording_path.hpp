#pragma once

#include "sdr_core/errors.hpp"

#include <filesystem>
#include <string>
#include <string_view>
#include <system_error>

namespace sdr_core {

// RecordingConfig/Python use UTF-8; native filesystem paths must never pass
// through the Windows ANSI code page, including artifact suffix construction.
[[nodiscard]] inline std::filesystem::path recording_path_from_utf8(
    const std::string& value
) {
    if (value.find('\0') != std::string::npos) {
        throw ConfigurationError("recording path contains an embedded NUL");
    }
    try {
        return std::filesystem::path(std::u8string(value.begin(), value.end()));
    } catch (const std::system_error&) {
        throw ConfigurationError("recording path is not valid UTF-8");
    }
}

[[nodiscard]] inline std::string recording_path_utf8(const std::filesystem::path& path) {
    const auto value = path.u8string();
    return {reinterpret_cast<const char*>(value.data()), value.size()};
}

[[nodiscard]] inline std::filesystem::path recording_path_with_suffix(
    std::filesystem::path path, const std::string_view suffix
) {
    path += std::string(suffix); // Only ASCII artifact suffixes, not a full narrow path.
    return path;
}

}  // namespace sdr_core
