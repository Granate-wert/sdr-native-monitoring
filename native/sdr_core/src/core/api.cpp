#include "sdr_core/api.hpp"

#include "sdr_core/types.hpp"
#include "sdr_core/version.hpp"

#include <chrono>
#include <thread>

#ifndef SDR_CORE_COMPILER_ID
#define SDR_CORE_COMPILER_ID "unknown"
#endif

#ifndef SDR_CORE_COMPILER_VERSION
#define SDR_CORE_COMPILER_VERSION "unknown"
#endif

#ifndef SDR_CORE_PLATFORM
#define SDR_CORE_PLATFORM "unknown"
#endif

#ifndef SDR_CORE_ARCHITECTURE
#define SDR_CORE_ARCHITECTURE "unknown"
#endif

#ifndef SDR_CORE_BUILD_TYPE
#define SDR_CORE_BUILD_TYPE "unknown"
#endif

#ifndef SDR_CORE_CUDA_COMPILED
#define SDR_CORE_CUDA_COMPILED 0
#endif

#ifndef SDR_CORE_PLUTO_COMPILED
#define SDR_CORE_PLUTO_COMPILED 0
#endif

namespace sdr_core {

BuildInfo build_info() {
    return BuildInfo{
        .version = std::string(version),
        .compiler = std::string(SDR_CORE_COMPILER_ID) + " " + SDR_CORE_COMPILER_VERSION,
        .platform = SDR_CORE_PLATFORM,
        .architecture = SDR_CORE_ARCHITECTURE,
        .build_type = SDR_CORE_BUILD_TYPE,
        .cuda_compiled = SDR_CORE_CUDA_COMPILED != 0,
        .pluto_compiled = SDR_CORE_PLUTO_COMPILED != 0,
    };
}

std::vector<std::string> available_backends() {
    std::vector<std::string> result{"cpu"};
#if SDR_CORE_PLUTO_COMPILED
    result.emplace_back("pluto-libiio");
#endif
#if SDR_CORE_CUDA_COMPILED
    result.emplace_back("cuda");
#endif
    return result;
}

SelfTestResult run_self_test() {
    const auto info = build_info();
    const auto backends = available_backends();
    const std::size_t expected_backends =
        1U + (info.pluto_compiled ? 1U : 0U) + (info.cuda_compiled ? 1U : 0U);
    const bool valid = !info.version.empty() && backends.size() == expected_backends &&
                       backends.front() == "cpu" &&
                       info.cuda_compiled == (SDR_CORE_CUDA_COMPILED != 0) &&
                       info.pluto_compiled == (SDR_CORE_PLUTO_COMPILED != 0) &&
                       contract_schema_version == 5U;
    return SelfTestResult{
        .ok = valid,
        .message = valid ? "portable CPU contracts are operational" : "build metadata is inconsistent",
    };
}

void sleep_for_milliseconds(const std::uint64_t milliseconds) {
    std::this_thread::sleep_for(std::chrono::milliseconds(milliseconds));
}

}  // namespace sdr_core
