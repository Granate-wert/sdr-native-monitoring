// Explicit mock-only microbenchmark for the native libiio -> canonical CI16
// boundary. It deliberately measures no radio, network, IIO daemon, FFT or
// Python/Qt path, and it persists no I/Q samples.

#include "sdr_pluto/pluto_backend.hpp"

#include <chrono>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {

struct Arguments {
    std::uint32_t buffer_samples{262144U};
    double warmup_seconds{0.2};
    double measurement_seconds{2.0};
};

[[nodiscard]] Arguments parse_arguments(const int argc, char** argv) {
    Arguments result;
    for (int index = 1; index < argc; ++index) {
        const std::string_view argument(argv[index]);
        if (index + 1 >= argc) {
            throw std::runtime_error("missing benchmark argument value");
        }
        const std::string_view value(argv[++index]);
        if (argument == "--buffer-samples") {
            result.buffer_samples = static_cast<std::uint32_t>(std::stoul(std::string(value)));
        } else if (argument == "--warmup-seconds") {
            result.warmup_seconds = std::stod(std::string(value));
        } else if (argument == "--measurement-seconds") {
            result.measurement_seconds = std::stod(std::string(value));
        } else {
            throw std::runtime_error("unsupported argument: " + std::string(argument));
        }
    }
    if (result.buffer_samples == 0U || !std::isfinite(result.warmup_seconds) ||
        !std::isfinite(result.measurement_seconds) || result.warmup_seconds < 0.0 ||
        result.measurement_seconds <= 0.0) {
        throw std::runtime_error("benchmark arguments must be positive");
    }
    return result;
}

struct PhaseResult {
    double elapsed_seconds{};
    std::uint64_t blocks{};
    std::uint64_t complex_samples{};
};

[[nodiscard]] PhaseResult run_phase(
    sdr_pluto::PlutoDevice& device,
    const double target_seconds
) {
    const auto started = std::chrono::steady_clock::now();
    PhaseResult result;
    while (true) {
        const auto block = device.refill();
        ++result.blocks;
        result.complex_samples += block.sample_count;
        result.elapsed_seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started
        ).count();
        if (result.elapsed_seconds >= target_seconds) {
            return result;
        }
    }
}

int run(const Arguments& arguments) {
    sdr_pluto::PlutoDevice device("usb:mock");
    const auto applied = device.configure({
        .source_id = "pluto-refill-bench",
        .context_uri = "usb:mock",
        .center_frequency_hz = 2'450'000'000.0,
        .sample_rate_hz = 61'440'000.0,
        .analog_bandwidth_hz = 56'000'000.0,
        .gain_mode = sdr_core::GainMode::Manual,
        .manual_gain_db = 20.0,
        .channel_index = 0U,
        .buffer_samples = arguments.buffer_samples,
        .schema_version = sdr_core::contract_schema_version,
    });
    device.start_stream();
    static_cast<void>(run_phase(device, arguments.warmup_seconds));
    const auto measurement = run_phase(device, arguments.measurement_seconds);
    const auto metrics = device.metrics();
    device.stop_stream();
    device.disconnect();

    if (metrics.refill_errors != 0U || metrics.output_pool_exhaustions != 0U ||
        metrics.short_reads != 0U || measurement.complex_samples == 0U) {
        throw std::runtime_error("mock refill benchmark encountered a transport accounting error");
    }
    const auto complex_rate = static_cast<double>(measurement.complex_samples) /
                              measurement.elapsed_seconds;
    std::cout << std::setprecision(17)
              << "{\"schema\":\"sdr-native-pluto-refill-microbench-v1\""
              << ",\"source_kind\":\"mock_libiio_no_radio\""
              << ",\"sample_format\":\"complex_int12_in_int16_le\""
              << ",\"buffer_samples\":" << arguments.buffer_samples
              << ",\"applied_sample_rate_hz\":" << applied.sample_rate_hz
              << ",\"measurement_seconds\":" << measurement.elapsed_seconds
              << ",\"blocks\":" << measurement.blocks
              << ",\"complex_samples\":" << measurement.complex_samples
              << ",\"observed_complex_sample_rate_hz\":" << complex_rate
              << ",\"observed_payload_bytes_per_second\":" << complex_rate * 4.0
              << ",\"not_verified\":[\"No physical receiver, USB, Ethernet, iiod, FFT, Python or Qt path was measured.\"]"
              << "}\n";
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        return run(parse_arguments(argc, argv));
    } catch (const std::exception& error) {
        std::cerr << "sdr_core_pluto_refill_bench: " << error.what() << '\n';
        return 1;
    }
}
