#include "sdr_core/persistence.hpp"

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr std::uint32_t default_frequency_bins = 4096U;
constexpr std::uint32_t default_power_bins = 256U;
constexpr std::uint32_t default_frames = 300U;

std::uint32_t parse_positive(const char* value, const char* label) {
    try {
        const auto parsed = std::stoul(value);
        if (parsed == 0U || parsed > std::numeric_limits<std::uint32_t>::max()) {
            throw std::out_of_range("range");
        }
        return static_cast<std::uint32_t>(parsed);
    } catch (...) {
        throw std::runtime_error(std::string(label) + " must be a positive uint32");
    }
}

sdr_core::SpectrumFrame make_frame(
    const std::uint32_t frequency_bins,
    const std::int64_t timestamp_ns,
    const std::uint64_t sequence
) {
    auto frequencies = std::make_shared<std::vector<double>>(frequency_bins);
    auto values = std::make_shared<std::vector<float>>(frequency_bins);
    for (std::uint32_t index = 0U; index < frequency_bins; ++index) {
        (*frequencies)[index] = 2.4e9 + static_cast<double>(index) * 1'000.0;
        (*values)[index] = -120.0F + static_cast<float>(index % 96U) * 1.25F;
    }
    sdr_core::SpectrumFrame frame;
    frame.frame_sequence = sequence;
    frame.timestamp_ns = timestamp_ns;
    frame.frequencies_hz = std::move(frequencies);
    frame.values = std::move(values);
    return frame;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        std::uint32_t frequency_bins = default_frequency_bins;
        std::uint32_t power_bins = default_power_bins;
        std::uint32_t frames = default_frames;
        for (int index = 1; index < argc; ++index) {
            const std::string argument = argv[index];
            if (argument == "--frequency-bins" && index + 1 < argc) {
                frequency_bins = parse_positive(argv[++index], "frequency-bins");
            } else if (argument == "--power-bins" && index + 1 < argc) {
                power_bins = parse_positive(argv[++index], "power-bins");
            } else if (argument == "--frames" && index + 1 < argc) {
                frames = parse_positive(argv[++index], "frames");
            } else {
                throw std::runtime_error("usage: --frequency-bins N --power-bins N --frames N");
            }
        }

        sdr_core::PersistenceConfig config;
        config.enabled = true;
        config.mode = sdr_core::PersistenceMode::ExponentialDecay;
        config.window_frames = 1U;
        config.half_life_seconds = 1.0;
        config.power_min_db = -140.0;
        config.power_max_db = 20.0;
        config.power_bins = power_bins;
        config.snapshot_rate_hz = 30.0;
        sdr_core::PersistenceAccumulator accumulator(config);
        const auto first = make_frame(frequency_bins, 1'000'000LL, 0U);
        static_cast<void>(accumulator.update(first));
        const auto started = std::chrono::steady_clock::now();
        std::uint64_t snapshots = 0U;
        for (std::uint32_t index = 1U; index <= frames; ++index) {
            auto frame = first;
            frame.frame_sequence = index;
            frame.timestamp_ns = 1'000'000LL + static_cast<std::int64_t>(index) * 1'000'000LL;
            if (accumulator.update(frame).has_value()) {
                ++snapshots;
            }
        }
        const auto elapsed_s = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started
        ).count();
        const auto cells = static_cast<std::uint64_t>(frequency_bins) * power_bins;
        std::cout << "{\"schema\":\"sdr-native-persistence-bench-v1\","
                  << "\"mode\":\"exponential-decay\","
                  << "\"frequency_bins\":" << frequency_bins << ','
                  << "\"power_bins\":" << power_bins << ','
                  << "\"frames\":" << frames << ','
                  << "\"cells\":" << cells << ','
                  << "\"snapshots\":" << snapshots << ','
                  << "\"elapsed_seconds\":" << elapsed_s << ','
                  << "\"update_rate_hz\":" << static_cast<double>(frames) / elapsed_s
                  << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
