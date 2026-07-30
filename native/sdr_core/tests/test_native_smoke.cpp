#include "sdr_core/api.hpp"

#include <algorithm>
#include <chrono>
#include <iostream>
#include <string>

int main() {
    const auto info = sdr_core::build_info();
    if (info.version.empty() || info.compiler.empty() || info.platform.empty() || info.architecture.empty()) {
        std::cerr << "invalid build_info" << std::endl;
        return 1;
    }
    if (info.cuda_compiled) {
        std::cerr << "CPU/Pluto build unexpectedly reports CUDA" << std::endl;
        return 2;
    }

    const auto backends = sdr_core::available_backends();
    const auto has_cpu = std::find(backends.begin(), backends.end(), "cpu") != backends.end();
    const auto has_pluto = std::find(backends.begin(), backends.end(), "pluto-libiio") != backends.end();
    if (!has_cpu || has_pluto != info.pluto_compiled) {
        std::cerr << "backend list does not match build features" << std::endl;
        return 3;
    }

    const auto self_test = sdr_core::run_self_test();
    if (!self_test.ok) {
        std::cerr << self_test.message << std::endl;
        return 4;
    }

    const auto started = std::chrono::steady_clock::now();
    sdr_core::sleep_for_milliseconds(2);
    const auto elapsed = std::chrono::steady_clock::now() - started;
    if (elapsed < std::chrono::milliseconds(1)) {
        std::cerr << "sleep helper returned too early" << std::endl;
        return 5;
    }

    std::cout << "sdr_core native smoke passed: " << info.version << std::endl;
    return 0;
}
