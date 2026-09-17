#include "sdr_hackrf/hackrf_live_factory.hpp"

#include "sdr_core/types.hpp"

#include <chrono>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

namespace {

constexpr auto warmup_duration = std::chrono::seconds(3);
constexpr auto measurement_duration = std::chrono::seconds(15);
constexpr double requested_sample_rate = 8'000'000.0;
constexpr double minimum_delivery_ratio = 0.95;
constexpr double minimum_fft_ratio = 0.90;
constexpr std::uint32_t fft_size = 4096U;
constexpr std::uint32_t hop_size = 2048U;

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

std::uint64_t delta(const std::uint64_t after, const std::uint64_t before) {
    if (after < before) {
        throw std::runtime_error("R13-D cumulative metric regressed");
    }
    return after - before;
}

std::string boolean(const bool value) { return value ? "true" : "false"; }

sdr_hackrf::HackrfLiveFactoryConfig profile() {
    return {
        .center_frequency_hz = 100'000'000.0,
        .sample_rate_hz = requested_sample_rate,
        .baseband_filter_hz = 7'000'000U,
        .lna_gain_db = 16U,
        .vga_gain_db = 20U,
        .rf_amplifier_enabled = false,
        .bias_tee_enabled = false,
        .fft_size = fft_size,
        .hop_size = hop_size,
        .window = sdr_core::WindowType::Hann,
        .detector = sdr_core::DetectorType::Sample,
        .slot_count = 32U,
        .ready_capacity = 24U,
        .dsp_output_capacity = 64U,
        .presentation_capacity = 64U,
        .configuration_generation = 1U,
        .source_id = "native.hackrf.r13d.fixed-band",
    };
}

}  // namespace

int main(const int argc, char** argv) {
    if (argc != 3 || std::string(argv[1]) != "--preflight-sha256" ||
        !is_sha256(argv[2])) {
        std::cerr << "R13-D requires --preflight-sha256 <64hex>\n";
        return 2;
    }
    const std::string preflight_sha256 = argv[2];

    try {
        auto session = sdr_hackrf::make_official_hackrf_runtime_dsp_session(profile());

        const auto warmup_started = std::chrono::steady_clock::now();
        while (std::chrono::steady_clock::now() - warmup_started < warmup_duration) {
            static_cast<void>(session->poll_spectrum_frames());
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        static_cast<void>(session->poll_spectrum_frames());
        const auto baseline = session->metrics();

        const auto measurement_started = std::chrono::steady_clock::now();
        std::uint64_t frames_polled{};
        std::uint64_t frame_drop_flags{};
        std::uint64_t invalid_frame_contracts{};
        while (std::chrono::steady_clock::now() - measurement_started < measurement_duration) {
            auto frames = session->poll_spectrum_frames();
            for (const auto& frame : frames) {
                ++frames_polled;
                if (frame.fft_size != fft_size || frame.hop_size != hop_size ||
                    frame.unit != sdr_core::SpectrumUnit::DbfsBin ||
                    frame.precision_mode != sdr_core::PrecisionMode::ReferenceF64) {
                    ++invalid_frame_contracts;
                }
                if (frame.dropped_iq_blocks_before != 0U ||
                    frame.dropped_samples_before != 0U ||
                    frame.dropped_fft_frames_before != 0U) {
                    ++frame_drop_flags;
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        const auto measurement_stopped = std::chrono::steady_clock::now();
        auto tail_frames = session->poll_spectrum_frames();
        for (const auto& frame : tail_frames) {
            ++frames_polled;
            if (frame.fft_size != fft_size || frame.hop_size != hop_size ||
                frame.unit != sdr_core::SpectrumUnit::DbfsBin ||
                frame.precision_mode != sdr_core::PrecisionMode::ReferenceF64) {
                ++invalid_frame_contracts;
            }
            if (frame.dropped_iq_blocks_before != 0U ||
                frame.dropped_samples_before != 0U ||
                frame.dropped_fft_frames_before != 0U) {
                ++frame_drop_flags;
            }
        }
        const auto observed = session->metrics();
        const auto elapsed =
            std::chrono::duration<double>(measurement_stopped - measurement_started).count();

        const auto shutdown = session->stop(std::chrono::seconds(5));
        const auto final_metrics = session->metrics();

        const auto samples_admitted = delta(
            observed.source.samples_admitted, baseline.source.samples_admitted
        );
        const auto samples_processed = delta(
            observed.processing.dsp.iq_samples_processed,
            baseline.processing.dsp.iq_samples_processed
        );
        const auto fft_frames_computed = delta(
            observed.processing.dsp.dsp.fft_frames_computed,
            baseline.processing.dsp.dsp.fft_frames_computed
        );
        const auto fft_frames_dropped = delta(
            observed.processing.dsp.dsp.fft_frames_dropped,
            baseline.processing.dsp.dsp.fft_frames_dropped
        );
        const auto presentation_drops = delta(
            observed.processing.dsp.presentation.dropped,
            baseline.processing.dsp.presentation.dropped
        );
        const auto source_loss_events = delta(
            observed.source.loss_events, baseline.source.loss_events
        );
        const auto source_sequence_discontinuities = delta(
            observed.processing.dsp.source_sequence_discontinuities,
            baseline.processing.dsp.source_sequence_discontinuities
        );
        const auto source_sample_index_discontinuities = delta(
            observed.processing.dsp.source_sample_index_discontinuities,
            baseline.processing.dsp.source_sample_index_discontinuities
        );
        const auto source_blocks_missing = delta(
            observed.processing.dsp.source_blocks_missing,
            baseline.processing.dsp.source_blocks_missing
        );
        const auto source_samples_missing = delta(
            observed.processing.dsp.source_samples_missing,
            baseline.processing.dsp.source_samples_missing
        );
        const auto source_timestamp_regressions = delta(
            observed.processing.dsp.source_timestamp_regressions,
            baseline.processing.dsp.source_timestamp_regressions
        );
        const auto worker_failures = delta(
            observed.processing.worker_failures, baseline.processing.worker_failures
        );
        const auto worker_abandoned_blocks = delta(
            observed.processing.worker_abandoned_blocks,
            baseline.processing.worker_abandoned_blocks
        );
        const auto sample_rate = elapsed > 0.0
            ? static_cast<double>(samples_admitted) / elapsed
            : 0.0;
        const auto processing_ratio = samples_admitted > 0U
            ? static_cast<double>(samples_processed) / static_cast<double>(samples_admitted)
            : 0.0;
        const auto fft_rate = elapsed > 0.0
            ? static_cast<double>(fft_frames_computed) / elapsed
            : 0.0;
        const auto minimum_fft_rate =
            minimum_fft_ratio * requested_sample_rate / static_cast<double>(hop_size);
        const bool cpu_backend_clean =
            observed.processing.dsp.dsp.active_backend ==
                sdr_core::ComputeBackendKind::Cpu &&
            observed.processing.dsp.dsp.backend_fallback_count == 0U &&
            observed.processing.dsp.dsp.backend_switch_count == 0U;

        const bool accepted =
            elapsed >= 14.5 &&
            sample_rate >= requested_sample_rate * minimum_delivery_ratio &&
            processing_ratio >= minimum_delivery_ratio &&
            fft_rate >= minimum_fft_rate && frames_polled != 0U &&
            source_loss_events == 0U && fft_frames_dropped == 0U &&
            presentation_drops == 0U && source_sequence_discontinuities == 0U &&
            source_sample_index_discontinuities == 0U && worker_failures == 0U &&
            source_blocks_missing == 0U && source_samples_missing == 0U &&
            source_timestamp_regressions == 0U && worker_abandoned_blocks == 0U &&
            frame_drop_flags == 0U && invalid_frame_contracts == 0U &&
            cpu_backend_clean && shutdown.complete() &&
            shutdown.processing.worker_abandoned_blocks == 0U;

        std::ostringstream json;
        json << std::setprecision(12)
             << "{\"schema\":\"sdr-native-r13d-hackrf-fft-evidence-v1\""
             << ",\"preflight_sha256\":\"" << preflight_sha256 << "\""
             << ",\"profile_id\":\"r13d-hackrf-8m-fft4096-cpu\""
             << ",\"profile\":{\"center_frequency_hz\":100000000"
             << ",\"sample_rate_hz\":8000000,\"baseband_filter_hz\":7000000"
             << ",\"lna_gain_db\":16,\"vga_gain_db\":20"
             << ",\"rf_amplifier_enabled\":false,\"bias_tee_enabled\":false"
             << ",\"fft_size\":4096,\"hop_size\":2048,\"window\":\"hann\""
             << ",\"unit\":\"dBFS/bin\",\"warmup_s\":3,\"measurement_s\":15}"
             << ",\"elapsed_s\":" << elapsed
             << ",\"samples_admitted\":" << samples_admitted
             << ",\"samples_processed\":" << samples_processed
             << ",\"observed_sample_rate\":" << sample_rate
             << ",\"processing_ratio\":" << processing_ratio
             << ",\"fft_frames_computed\":" << fft_frames_computed
             << ",\"analytical_fft_frames_per_second\":" << fft_rate
             << ",\"minimum_accepted_fft_frames_per_second\":" << minimum_fft_rate
             << ",\"frames_polled\":" << frames_polled
             << ",\"source_loss_events\":" << source_loss_events
             << ",\"fft_frames_dropped\":" << fft_frames_dropped
             << ",\"presentation_drops\":" << presentation_drops
             << ",\"source_sequence_discontinuities\":"
             << source_sequence_discontinuities
             << ",\"source_sample_index_discontinuities\":"
             << source_sample_index_discontinuities
             << ",\"source_blocks_missing\":" << source_blocks_missing
             << ",\"source_samples_missing\":" << source_samples_missing
             << ",\"source_timestamp_regressions\":" << source_timestamp_regressions
             << ",\"worker_failures\":" << worker_failures
             << ",\"worker_abandoned_blocks\":" << worker_abandoned_blocks
             << ",\"cpu_backend_clean\":" << boolean(cpu_backend_clean)
             << ",\"frame_drop_flags\":" << frame_drop_flags
             << ",\"invalid_frame_contracts\":" << invalid_frame_contracts
             << ",\"shutdown\":{\"stop_rx_status\":"
             << shutdown.source_quiesce.stop_rx_status
             << ",\"callbacks_quiescent\":"
             << boolean(shutdown.source_quiesce.callbacks_quiescent)
             << ",\"worker_joined\":" << boolean(shutdown.processing.worker_joined)
             << ",\"worker_abandoned_blocks\":"
             << shutdown.processing.worker_abandoned_blocks
             << ",\"close_status\":" << shutdown.source_finalize.close_status
             << ",\"exit_status\":" << shutdown.source_finalize.exit_status << "}"
             << ",\"shutdown_tail_loss_events\":"
             << delta(final_metrics.source.loss_events, observed.source.loss_events)
             << ",\"device_overrun_counter_available\":false"
             << ",\"continuity_status\":\"host_clean_continuity_unverified\""
             << ",\"claim_scope\":\"hackrf_fixed_band_ci8_cpu_fft_scalar_only\""
             << ",\"accepted\":" << boolean(accepted) << "}";

        std::cout << "R13D_EVIDENCE_JSON=" << json.str() << '\n';
        std::cout.flush();
        if (!shutdown.complete()) {
            std::_Exit(3);
        }
        return accepted ? 0 : 3;
    } catch (const std::exception&) {
        std::cerr << "R13-D failed closed: sanitized runtime failure\n";
        return 4;
    }
}
