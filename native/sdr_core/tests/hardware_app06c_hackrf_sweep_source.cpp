#include "sdr_hackrf/hackrf_official_rx_port.hpp"
#include "sdr_hackrf/hackrf_sweep_session.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

namespace {

constexpr auto observation_duration = std::chrono::seconds(5);
constexpr const char* confirmation = "I-CONFIRM-APP06C-HACKRF-SWEEP-RX";

std::array<std::uint32_t, 4> parse_serial(const std::string& value) {
    if (value.size() != 32U || !std::all_of(value.begin(), value.end(),
            [](const unsigned char character) { return std::isxdigit(character) != 0; })) {
        throw std::invalid_argument("expected serial must be exactly 32 hexadecimal digits");
    }
    std::array<std::uint32_t, 4> result{};
    for (std::size_t index = 0U; index < result.size(); ++index) {
        result[index] = static_cast<std::uint32_t>(
            std::stoul(value.substr(index * 8U, 8U), nullptr, 16)
        );
    }
    return result;
}

void write_once(const std::filesystem::path& output, const std::string& payload) {
    if (std::filesystem::exists(output)) {
        throw std::runtime_error("Sweep evidence output already exists");
    }
    if (!output.parent_path().empty()) {
        std::filesystem::create_directories(output.parent_path());
    }
    std::ofstream stream(output, std::ios::binary | std::ios::out);
    if (!stream) {
        throw std::runtime_error("Sweep evidence output cannot be created");
    }
    stream << payload << '\n';
    stream.flush();
    if (!stream) {
        throw std::runtime_error("Sweep evidence output cannot be finalized");
    }
}

const char* boolean(const bool value) { return value ? "true" : "false"; }

}  // namespace

int main(const int argc, char** argv) {
    if (argc != 7 || std::string(argv[1]) != "--confirm" ||
        std::string(argv[2]) != confirmation ||
        std::string(argv[3]) != "--expected-serial" ||
        std::string(argv[5]) != "--output") {
        std::cerr << "Explicit --confirm " << confirmation
                  << " --expected-serial <32hex> --output <new-file> required\n";
        return 2;
    }
    std::array<std::uint32_t, 4> expected{};
    try {
        expected = parse_serial(argv[4]);
    } catch (const std::exception&) {
        std::cerr << "Expected HackRF serial is malformed\n";
        return 2;
    }
    const std::filesystem::path output = argv[6];
    if (std::filesystem::exists(output)) {
        std::cerr << "Evidence output already exists\n";
        return 2;
    }

    try {
        sdr_hackrf::HackrfSweepProfile profile;
        profile.sequence.ranges = {{2400U, 2480U}};
        profile.sequence.step_width_hz = 20'000'000U;
        profile.sequence.offset_hz = 7'500'000U;
        profile.sequence.style = sdr_hackrf::HackrfSweepStyle::Interleaved;
        profile.sequence.max_transfer_blocks = 16U;
        profile.sample_rate_hz = 20'000'000.0;
        profile.baseband_filter_hz = 15'000'000U;
        profile.ready_capacity = 128U;

        auto session = sdr_hackrf::HackrfSweepSession::start(
            sdr_hackrf::make_official_hackrf_sweep_port(expected), profile
        );
        const auto started = std::chrono::steady_clock::now();
        std::uint64_t consumed{};
        std::uint64_t new_scans{};
        std::uint64_t gaps{};
        std::uint64_t min_header = UINT64_MAX;
        std::uint64_t max_header{};
        sdr_hackrf::HackrfSweepQueuedBlock block;
        while (std::chrono::steady_clock::now() - started < observation_duration) {
            if (!session->try_pop(block)) {
                std::this_thread::sleep_for(std::chrono::microseconds(200));
                continue;
            }
            ++consumed;
            new_scans += block.new_scan ? 1U : 0U;
            gaps += block.gap_before ? 1U : 0U;
            min_header = std::min(min_header, block.reported_tuned_frequency_hz);
            max_header = std::max(max_header, block.reported_tuned_frequency_hz);
        }
        const auto elapsed = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started
        ).count();
        const auto stopped = session->stop(std::chrono::seconds(5));
        const auto metrics = session->metrics();
        const bool observed = stopped.clean() && consumed > 0U &&
                              metrics.sequence.has_value() &&
                              metrics.sequence->blocks_admitted >= consumed;

        std::ostringstream json;
        json << std::setprecision(12)
             << "{\"schema\":\"app06c-hackrf-sweep-source-observation-v1\""
             << ",\"profile\":{\"start_mhz\":2400,\"stop_mhz\":2480"
             << ",\"step_hz\":20000000,\"offset_hz\":7500000"
             << ",\"sample_rate_requested_hz\":20000000"
             << ",\"filter_requested_hz\":15000000"
             << ",\"amplifier_enabled\":false,\"bias_tee_enabled\":false}"
             << ",\"usb_api_readback\":" << session->observed_usb_api_version()
             << ",\"transfer_bytes\":" << session->transfer_bytes()
             << ",\"elapsed_s\":" << elapsed
             << ",\"callbacks_seen\":" << metrics.callbacks_seen
             << ",\"callback_bytes_seen\":" << metrics.callback_bytes_seen
             << ",\"blocks_queued\":" << metrics.blocks_queued
             << ",\"blocks_consumed\":" << consumed
             << ",\"blocks_abandoned_at_stop\":" << stopped.abandoned_blocks
             << ",\"ready_full_drops\":" << metrics.ready_full_drops
             << ",\"callback_gate_drops\":" << metrics.callback_gate_drops
             << ",\"logical_scans\":" << new_scans
             << ",\"gap_before_blocks\":" << gaps
             << ",\"min_reported_header_hz\":" << (consumed ? min_header : 0U)
             << ",\"max_reported_header_hz\":" << max_header
             << ",\"sequence_blocks_admitted\":"
             << (metrics.sequence ? metrics.sequence->blocks_admitted : 0U)
             << ",\"sequence_unlocated_rejections\":"
             << (metrics.sequence ? metrics.sequence->unlocated_rejections : 0U)
             << ",\"sequence_downstream_drops\":"
             << (metrics.sequence ? metrics.sequence->downstream_drops : 0U)
             << ",\"stop_status\":" << stopped.stop_rx_status
             << ",\"first_close_error\":" << stopped.first_close_error
             << ",\"callbacks_quiescent\":" << boolean(stopped.callbacks_quiescent)
             << ",\"close_status\":" << stopped.close_status
             << ",\"exit_status\":" << stopped.exit_status
             << ",\"device_overrun_counter_available\":false"
             << ",\"rf_coverage_verified\":false"
             << ",\"continuous_iq_or_pd_verified\":false"
             << ",\"source_observed\":" << boolean(observed) << "}";
        write_once(output, json.str());
        std::cout << (observed ? "SOURCE OBSERVED" : "SOURCE NOT QUALIFIED") << '\n';
        if (!stopped.complete()) {
            // The SDK may still reference session memory. Evidence is durable;
            // fail-stop rather than invoking its destructor after bad close.
            std::cout.flush();
            std::_Exit(3);
        }
        return observed ? 0 : 3;
    } catch (const std::exception&) {
        try {
            write_once(output,
                "{\"schema\":\"app06c-hackrf-sweep-source-observation-v1\","
                "\"source_observed\":false,\"failure\":\"sanitized_runtime_failure\"}");
        } catch (...) {
        }
        std::cerr << "HackRF Sweep source failed closed\n";
        return 4;
    }
}
