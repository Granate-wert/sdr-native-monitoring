#include "sdr_hackrf/hackrf_official_rx_port.hpp"
#include "sdr_hackrf/hackrf_rx_session.hpp"

#include "sdr_core/types.hpp"

#include <chrono>
#include <cctype>
#include <cstdlib>
#include <cstdint>
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
constexpr double minimum_rate = 9'000'000.0;

bool is_sha256(const std::string& value) {
    if (value.size() != 64U) {
        return false;
    }
    for (const auto character : value) {
        if (!std::isxdigit(static_cast<unsigned char>(character))) {
            return false;
        }
    }
    return true;
}

void write_once(const std::filesystem::path& output, const std::string& payload) {
    if (std::filesystem::exists(output)) {
        throw std::runtime_error("R11-H evidence output already exists");
    }
    if (!output.parent_path().empty()) {
        std::filesystem::create_directories(output.parent_path());
    }
    std::ofstream stream(output, std::ios::binary | std::ios::out);
    if (!stream) {
        throw std::runtime_error("R11-H evidence output cannot be created");
    }
    stream << payload << '\n';
    stream.flush();
    if (!stream) {
        throw std::runtime_error("R11-H evidence output cannot be finalized");
    }
}

std::string boolean(const bool value) { return value ? "true" : "false"; }

}  // namespace

int main(const int argc, char** argv) {
    if (argc != 5 || std::string(argv[1]) != "--preflight-sha256" ||
        std::string(argv[3]) != "--output" || !is_sha256(argv[2])) {
        std::cerr << "R11-H requires --preflight-sha256 <64hex> --output <new-file>\n";
        return 2;
    }
    const std::string preflight_sha256 = argv[2];
    const std::filesystem::path output = argv[4];
    if (std::filesystem::exists(output)) {
        std::cerr << "R11-H evidence output already exists\n";
        return 2;
    }

    try {
        auto session = sdr_hackrf::HackrfRxSession::start(
            sdr_hackrf::make_official_hackrf_rx_port()
        );
        const auto started = std::chrono::steady_clock::now();
        std::uint64_t consumed_blocks{};
        std::uint64_t consumed_samples{};
        std::uint64_t sequence_gaps{};
        std::uint64_t sample_index_gaps{};
        std::uint64_t dropped_flags{};
        std::uint64_t expected_sequence{};
        std::uint64_t expected_sample_index{};

        while (std::chrono::steady_clock::now() - started < observation_duration) {
            sdr_hackrf::HackrfRxLease lease;
            if (!session->try_pop(lease)) {
                std::this_thread::sleep_for(std::chrono::microseconds(100));
                continue;
            }
            const auto& metadata = lease.metadata();
            if (metadata.source_sequence != expected_sequence) {
                ++sequence_gaps;
            }
            if (metadata.first_sample_index != expected_sample_index) {
                ++sample_index_gaps;
            }
            if (sdr_core::has_flag(metadata.quality_flags, sdr_core::QualityFlag::IqDropped)) {
                ++dropped_flags;
            }
            expected_sequence = metadata.source_sequence + 1U;
            expected_sample_index = metadata.first_sample_index + metadata.sample_count;
            ++consumed_blocks;
            consumed_samples += metadata.sample_count;
            lease.reset();

            if (session->metrics().loss_events != 0U) {
                break;
            }
        }

        const auto stopped_at = std::chrono::steady_clock::now();
        const auto elapsed = std::chrono::duration<double>(stopped_at - started).count();
        const auto pre_stop = session->metrics();
        const auto shutdown = session->stop(std::chrono::seconds(5));
        const auto final_metrics = session->metrics();
        const auto observed_rate = elapsed > 0.0
            ? static_cast<double>(pre_stop.samples_admitted) / elapsed
            : 0.0;
        const auto steady_state_loss =
            pre_stop.malformed_callbacks + pre_stop.short_callbacks +
            pre_stop.oversized_callbacks + pre_stop.lock_contention_drops +
            pre_stop.pool_exhaustion_drops + pre_stop.queue_full_drops;
        const bool accepted =
            elapsed >= 4.5 && pre_stop.callbacks_total != 0U &&
            consumed_blocks != 0U && observed_rate >= minimum_rate &&
            steady_state_loss == 0U && sequence_gaps == 0U &&
            sample_index_gaps == 0U && dropped_flags == 0U && shutdown.complete();

        std::ostringstream json;
        json << std::setprecision(12)
             << "{\"schema\":\"sdr-native-r11h-hackrf-rx-evidence-v1\""
             << ",\"preflight_sha256\":\"" << preflight_sha256 << "\""
             << ",\"profile\":{\"center_frequency_hz\":100000000"
             << ",\"sample_rate_hz\":10000000,\"baseband_filter_hz\":8000000"
             << ",\"lna_gain_db\":16,\"vga_gain_db\":20"
             << ",\"rf_amplifier_enabled\":false,\"bias_tee_enabled\":false"
             << ",\"duration_s\":5,\"slot_count\":32,\"ready_capacity\":24}"
             << ",\"transfer_bytes\":" << session->transfer_bytes()
             << ",\"elapsed_s\":" << elapsed
             << ",\"callbacks_total\":" << pre_stop.callbacks_total
             << ",\"samples_admitted\":" << pre_stop.samples_admitted
             << ",\"observed_sample_rate\":" << observed_rate
             << ",\"consumed_blocks\":" << consumed_blocks
             << ",\"consumed_samples\":" << consumed_samples
             << ",\"sequence_gaps\":" << sequence_gaps
             << ",\"sample_index_gaps\":" << sample_index_gaps
             << ",\"iq_dropped_flags\":" << dropped_flags
             << ",\"steady_state_loss_events\":" << steady_state_loss
             << ",\"ready_high_water\":" << pre_stop.ready_high_water
             << ",\"slots_high_water\":" << pre_stop.slots_high_water
             << ",\"shutdown_tail_callbacks\":" << final_metrics.callbacks_after_stop
             << ",\"shutdown_tail_loss_events\":"
             << (final_metrics.loss_events - pre_stop.loss_events)
             << ",\"stop_status\":" << shutdown.stop_rx_status
             << ",\"callbacks_quiescent\":" << boolean(shutdown.callbacks_quiescent)
             << ",\"abandoned_blocks\":" << shutdown.abandoned_blocks
             << ",\"close_status\":" << shutdown.close_status
             << ",\"exit_status\":" << shutdown.exit_status
             << ",\"device_overrun_counter_available\":false"
             << ",\"continuity_status\":\"host_clean_continuity_unverified\""
             << ",\"accepted\":" << boolean(accepted) << "}";
        write_once(output, json.str());
        std::cout << (accepted ? "R11-H ACCEPTED" : "R11-H REJECTED") << '\n';
        std::cout.flush();
        if (!shutdown.complete()) {
            // The callback may still retain the session context. Evidence is
            // already durable; process fail-stop avoids destroying that context.
            std::_Exit(3);
        }
        return accepted ? 0 : 3;
    } catch (const std::exception& error) {
        std::ostringstream json;
        json << "{\"schema\":\"sdr-native-r11h-hackrf-rx-evidence-v1\""
             << ",\"preflight_sha256\":\"" << preflight_sha256 << "\""
             << ",\"accepted\":false,\"failure\":\"sanitized_runtime_failure\"}";
        try {
            write_once(output, json.str());
        } catch (...) {
        }
        std::cerr << "R11-H failed closed: " << error.what() << '\n';
        return 4;
    }
}
