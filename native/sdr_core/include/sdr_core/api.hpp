#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace sdr_core {

struct BuildInfo {
    std::string version;
    std::string compiler;
    std::string platform;
    std::string architecture;
    std::string build_type;
    bool cuda_compiled;
    bool pluto_compiled;
};

struct SelfTestResult {
    bool ok;
    std::string message;
};

[[nodiscard]] BuildInfo build_info();
[[nodiscard]] std::vector<std::string> available_backends();
[[nodiscard]] SelfTestResult run_self_test();
void sleep_for_milliseconds(std::uint64_t milliseconds);

}  // namespace sdr_core
