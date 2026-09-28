#include "sdr_hackrf/hackrf_official_rx_port.hpp"
#include "sdr_hackrf/hackrf_sweep_runtime_analysis_session.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>

namespace {

constexpr auto observation_duration = std::chrono::seconds(5);
constexpr const char* confirmation = "I-CONFIRM-APP06C-HACKRF-SWEEP-RUNTIME-RX";

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
        throw std::runtime_error("Sweep runtime evidence output already exists");
    }
    if (!output.parent_path().empty()) {
        std::filesystem::create_directories(output.parent_path());
    }
    std::ofstream stream(output, std::ios::binary | std::ios::out);
    if (!stream) {
        throw std::runtime_error("Sweep runtime evidence output cannot be created");
    }
    stream << payload << '\n';
    stream.flush();
    if (!stream) {
        throw std::runtime_error("Sweep runtime evidence output cannot be finalized");
    }
}

const char* boolean(const bool value) { return value ? "true" : "false"; }

void consume(sdr_hackrf::HackrfSweepPublication publication,
             std::uint64_t& complete,
             std::uint64_t& gap,
             std::uint64_t& partial) {
    if (const auto* line = std::get_if<sdr_core::SweepLineFrame>(&publication)) {
        complete += line->state == sdr_core::SweepLineState::Complete ? 1U : 0U;
        gap += line->state == sdr_core::SweepLineState::Gap ? 1U : 0U;
    } else if (const auto* progress =
                   std::get_if<sdr_core::SweepProgressFrame>(&publication)) {
        partial += !progress->pending_segment_indices.empty() ? 1U : 0U;
    }
}

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
        sdr_hackrf::HackrfSweepRuntimeAnalysisConfig config;
        config.analysis.acquisition.sequence.ranges = {{2400U, 2480U}};
        config.analysis.acquisition.sequence.step_width_hz = 20'000'000U;
        config.analysis.acquisition.sequence.offset_hz = 7'500'000U;
        config.analysis.acquisition.sequence.style = sdr_hackrf::HackrfSweepStyle::Interleaved;
        config.analysis.acquisition.sequence.max_transfer_blocks = 16U;
        config.analysis.acquisition.ready_capacity = 128U;
        config.analysis.source.source_type = sdr_core::SourceType::LiveIq;
        config.analysis.source.source_id = "hackrf:diagnostic-sweep-runtime";
        config.analysis.source.display_name = "HackRF Sweep runtime diagnostic";
        config.analysis.source.backend_id = "native.libhackrf.sweep.v1";
        config.analysis.fft_size = 4096U;
        config.preview_rate_hz = 50U;

        auto session = sdr_hackrf::HackrfSweepRuntimeAnalysisSession::start(
            sdr_hackrf::make_official_hackrf_sweep_port(expected), std::move(config)
        );
        std::uint64_t returned_complete{};
        std::uint64_t returned_gap{};
        std::uint64_t returned_partial{};
        const auto started = std::chrono::steady_clock::now();
        while (std::chrono::steady_clock::now() - started < observation_duration) {
            for (std::uint32_t item = 0U; item < 2U; ++item) {
                consume(session->poll_next_publication(), returned_complete,
                        returned_gap, returned_partial);
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        const auto stopped = session->stop(std::chrono::seconds(5));
        for (std::uint32_t item = 0U; item < 2U; ++item) {
            consume(session->poll_next_publication(), returned_complete,
                    returned_gap, returned_partial);
        }
        const auto metrics = session->metrics();
        const bool observed = stopped.clean() && metrics.worker_joined &&
                              !metrics.worker_failed && !metrics.lifecycle_open &&
                              metrics.source.callbacks_active == 0U &&
                              metrics.source.ready_depth == 0U &&
                              metrics.analysis.accepted_blocks > 0U &&
                              metrics.analysis.dsp.fft_frames_computed ==
                                  metrics.analysis.accepted_blocks &&
                              metrics.analysis.lines.completed_lines > 0U &&
                              returned_complete > 0U;
        std::ostringstream json;
        json << "{\"schema\":\"app06c-hackrf-sweep-owned-runtime-observation-v1\""
             << ",\"observation_seconds\":5"
             << ",\"same_expected_serial_enforced\":true"
             << ",\"sample_rate_requested_hz\":20000000"
             << ",\"amplifier_enabled\":false,\"bias_tee_enabled\":false"
             << ",\"source_blocks_queued\":" << metrics.source.blocks_queued
             << ",\"source_blocks_popped\":" << metrics.source.blocks_popped
             << ",\"source_blocks_abandoned\":" << metrics.source.blocks_abandoned
             << ",\"source_ready_full_drops\":" << metrics.source.ready_full_drops
             << ",\"worker_blocks_processed\":" << metrics.worker_blocks_processed
             << ",\"analytical_fft_computed\":" << metrics.analysis.dsp.fft_frames_computed
             << ",\"numerical_complete_lines\":" << metrics.analysis.lines.completed_lines
             << ",\"numerical_gap_lines\":" << metrics.analysis.lines.gapped_lines
             << ",\"terminal_complete_returned\":" << returned_complete
             << ",\"terminal_gap_returned\":" << returned_gap
             << ",\"partial_previews_returned\":" << returned_partial
             << ",\"terminal_superseded\":" << metrics.terminal_superseded
             << ",\"progress_superseded\":" << metrics.progress_superseded
             << ",\"stop_complete\":" << boolean(stopped.complete())
             << ",\"stop_clean\":" << boolean(stopped.clean())
             << ",\"worker_joined\":" << boolean(metrics.worker_joined)
             << ",\"worker_failed\":" << boolean(metrics.worker_failed)
             << ",\"rf_coverage_verified\":false"
             << ",\"common_ui_published\":false"
             << ",\"dwm_or_gui_latency_verified\":false"
             << ",\"device_overrun_counter_available\":false"
             << ",\"observed\":" << boolean(observed) << "}";
        write_once(output, json.str());
        std::cout << (observed ? "OWNED NATIVE SWEEP OBSERVED" :
                                 "OWNED NATIVE SWEEP NOT QUALIFIED") << '\n';
        if (!stopped.complete()) {
            std::cout.flush();
            std::_Exit(3);
        }
        return observed ? 0 : 3;
    } catch (const std::exception&) {
        try {
            write_once(output,
                "{\"schema\":\"app06c-hackrf-sweep-owned-runtime-observation-v1\","
                "\"observed\":false,\"failure\":\"sanitized_runtime_failure\"}");
        } catch (...) {
        }
        std::cerr << "HackRF Sweep owned runtime failed closed\n";
        return 4;
    }
}
