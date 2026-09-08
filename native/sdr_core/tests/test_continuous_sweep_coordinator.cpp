#include "sdr_pluto/continuous_sweep_coordinator.hpp"

#include "sdr_core/errors.hpp"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <thread>

namespace {

[[nodiscard]] sdr_pluto::FixedBandConfig fixed_config(const double center_hz) {
    sdr_pluto::FixedBandConfig result;
    result.device = {
        .source_id = "r10d-coordinator-mock",
        .context_uri = "usb:mock",
        .center_frequency_hz = center_hz,
        .sample_rate_hz = 3'000'000.0,
        .analog_bandwidth_hz = 1'500'000.0,
        .gain_mode = sdr_core::GainMode::Manual,
        .manual_gain_db = 20.0,
        .channel_index = 0U,
        .buffer_samples = 4096U,
    };
    result.dsp = {
        .fft_size = 1024U,
        .hop_size = 512U,
        .window = sdr_core::WindowType::Hann,
        .detector = sdr_core::DetectorType::Sample,
        .unit = sdr_core::SpectrumUnit::DbfsBin,
        .precision_mode = sdr_core::PrecisionMode::AccurateF32F64Accum,
        .batch_size = 4U,
        .averaging_frames = 1U,
    };
    result.acquisition_queue_capacity = 4U;
    result.spectrum_queue_capacity = 2U;
    result.snapshot_rate_hz = 120.0;
    result.discard_blocks_after_start = 1U;
    return result;
}

[[nodiscard]] sdr_pluto::ContinuousSweepCoordinatorConfig coordinator_config() {
    return {
        .epoch = 71U,
        .display_start_hz = 2'449'000'000.0,
        .display_stop_hz = 2'451'000'000.0,
        .usable_window_hz = 2'000'000.0,
        .analysis_bins_per_usable_window = 512U,
        .output_queue_capacity = 8U,
        .segment_frame_timeout_ms = 1000U,
        .segments = {
            {
                .fixed_band = fixed_config(2'449'500'000.0),
                .usable_start_hz = 2'449'000'000.0,
                .usable_stop_hz = 2'450'100'000.0,
            },
            {
                .fixed_band = fixed_config(2'450'500'000.0),
                .usable_start_hz = 2'449'900'000.0,
                .usable_stop_hz = 2'451'000'000.0,
            },
        },
    };
}

[[nodiscard]] sdr_pluto::ContinuousSweepCoordinatorConfig single_window_config() {
    auto result = sdr_pluto::ContinuousSweepCoordinatorConfig{
        .epoch = 72U,
        .display_start_hz = 2'449'500'000.0,
        .display_stop_hz = 2'450'500'000.0,
        .line_snapshot_rate_hz = 2'000.0,
        .output_queue_capacity = 8U,
        .segment_frame_timeout_ms = 1000U,
        .segments = {{
            .fixed_band = fixed_config(2'450'000'000.0),
            .usable_start_hz = 2'449'500'000.0,
            .usable_stop_hz = 2'450'500'000.0,
        }},
    };
    // Make one mock refill contain many ready overlapping FFT frames.  The
    // coordinator output remains eight latest-wins lines, while the internal
    // relay must retain the complete bounded post-DSP burst long enough for
    // this worker to drain it.
    result.segments.front().fixed_band.device.buffer_samples = 65'536U;
    result.segments.front().fixed_band.snapshot_rate_hz = 2'000.0;
    return result;
}

[[nodiscard]] bool wait_for_completed(
    sdr_pluto::ContinuousSweepCoordinator& coordinator,
    const std::uint64_t minimum
) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (std::chrono::steady_clock::now() < deadline) {
        if (coordinator.metrics().completed_lines >= minimum) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    return false;
}

[[nodiscard]] bool contains_reason(
    const sdr_core::SweepLineFrame& line,
    const sdr_core::SweepLineGapReason reason
) {
    for (const auto item : line.gap_reasons) {
        if (item == reason) return true;
    }
    return false;
}

}  // namespace

int main() {
    try {
        // The coordinator owns one native receiver/engine, retunes only inside
        // its worker and emits a multi-generation reduced line; Python/Qt are
        // not involved in the test path.
        sdr_pluto::ContinuousSweepCoordinator coordinator("usb:mock");
        coordinator.configure(coordinator_config());
        coordinator.start();
        if (!wait_for_completed(coordinator, 2U)) return 1;
        const auto lines = coordinator.poll_lines(0U);
        bool saw_complete = false;
        for (const auto& line : lines) {
            if (line.state == sdr_core::SweepLineState::Complete) {
                saw_complete = line.epoch == 71U && line.segment_generations.size() == 2U &&
                    line.segment_generations[0].config_generation != 0U &&
                    line.segment_generations[1].config_generation != 0U &&
                    line.values->size() == 512U &&
                    line.analysis_window_hz == 2'000'000.0 &&
                    line.analysis_bins_per_usable_window == 512U &&
                    line.physical_fft_size == 1024U &&
                    std::abs(line.target_spacing_hz - (2'000'000.0 / 512.0)) < 1e-9 &&
                    std::abs(line.physical_fft_bin_width_hz - (3'000'000.0 / 1024.0)) < 1e-9;
            }
        }
        if (!saw_complete) return 2;
        const auto applied = coordinator.applied_segments();
        if (applied.size() != 2U || applied[0].sample_rate_hz != 3'000'000.0 ||
            applied[1].analog_bandwidth_hz != 1'500'000.0 ||
            applied[0].config_generation == 0U || applied[1].config_generation == 0U) return 7;
        const auto evidence_metrics = coordinator.metrics();
        if (evidence_metrics.device_iq_samples == 0U || evidence_metrics.analytical_fft_frames == 0U ||
            evidence_metrics.device_iq_blocks < 2U ||
            evidence_metrics.completed_current_generation_fft_frames.size() != 2U ||
            evidence_metrics.completed_current_generation_fft_frames[0] == 0U ||
            evidence_metrics.completed_current_generation_fft_frames[1] == 0U ||
            evidence_metrics.source_sequence_discontinuities != 0U ||
            evidence_metrics.source_sample_index_discontinuities != 0U ||
            evidence_metrics.source_timestamp_regressions != 0U ||
            evidence_metrics.source_estimated_timestamp_blocks == 0U ||
            evidence_metrics.hardware_overflow_counter_available ||
            evidence_metrics.acquisition_queue_high_water == 0U ||
            evidence_metrics.spectrum_queue_high_water == 0U) return 8;
        if (!evidence_metrics.completed_line_analysis_geometry_available ||
            evidence_metrics.completed_line_analysis_window_hz != 2'000'000.0 ||
            evidence_metrics.completed_line_analysis_bins_per_usable_window != 512U ||
            evidence_metrics.completed_line_physical_fft_size != 1024U ||
            std::abs(evidence_metrics.completed_line_physical_fft_bin_width_hz -
                     (3'000'000.0 / 1024.0)) > 1e-9 ||
            evidence_metrics.completed_line_analysis_geometry_mismatches != 0U) return 11;
        coordinator.stop();
        if (coordinator.state() != sdr_core::EngineState::Stopped || coordinator.metrics().has_error) return 3;

        // A span already inside one usable 36 MHz-style window must keep the
        // receiver configured while it emits consecutive lines.  Retuning for
        // each line would make LPS a retune benchmark rather than a spectrum
        // throughput measurement.
        sdr_pluto::ContinuousSweepCoordinator single_window("usb:mock");
        single_window.configure(single_window_config());
        single_window.start();
        if (!wait_for_completed(single_window, 4U)) return 9;
        const auto single_window_metrics = single_window.metrics();
        single_window.stop();
        if (single_window_metrics.segment_reconfigurations != 1U ||
            single_window_metrics.device_iq_samples == 0U ||
            single_window_metrics.analytical_fft_frames < single_window_metrics.completed_lines ||
            single_window_metrics.line_relay_queue_capacity < 134U ||
            single_window_metrics.line_relay_queue_high_water == 0U ||
            single_window_metrics.line_relay_snapshots_superseded != 0U ||
            single_window_metrics.completed_current_generation_fft_frames.size() != 1U ||
            single_window_metrics.completed_current_generation_fft_frames[0] < single_window_metrics.completed_lines ||
            single_window.metrics().has_error) {
            std::cerr << "single-window metrics: reconfig=" << single_window_metrics.segment_reconfigurations
                << " iq=" << single_window_metrics.device_iq_samples
                << " fft=" << single_window_metrics.analytical_fft_frames
                << " lines=" << single_window_metrics.completed_lines
                << " capacity=" << single_window_metrics.line_relay_queue_capacity
                << " high_water=" << single_window_metrics.line_relay_queue_high_water
                << " superseded=" << single_window_metrics.line_relay_snapshots_superseded
                << " generation_count=" << single_window_metrics.completed_current_generation_fft_frames.size()
                << " generation_fft=" << (single_window_metrics.completed_current_generation_fft_frames.empty()
                    ? 0U : single_window_metrics.completed_current_generation_fft_frames[0])
                << " error=" << single_window.metrics().has_error << std::endl;
            return 10;
        }

        // Scalar evidence can release completed line buffers entirely in the
        // native data plane. No Python spectrum-vector construction is needed.
        sdr_pluto::ContinuousSweepCoordinator native_discard("usb:mock");
        native_discard.configure(coordinator_config());
        native_discard.start();
        if (!wait_for_completed(native_discard, 2U)) return 13;
        if (native_discard.discard_lines(0U) == 0U) return 14;
        native_discard.stop();
        if (native_discard.metrics().has_error) return 15;

        // Cancellation during an in-progress RF epoch becomes an explicit
        // terminal gap rather than a partial stitch or a hidden retry.
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "100");
        sdr_pluto::ContinuousSweepCoordinator cancelled("usb:mock");
        cancelled.configure(coordinator_config());
        cancelled.start();
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
        cancelled.stop();
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "");
        const auto cancelled_lines = cancelled.poll_lines(0U);
        bool saw_cancellation_gap = false;
        for (const auto& line : cancelled_lines) {
            saw_cancellation_gap = saw_cancellation_gap ||
                (line.state == sdr_core::SweepLineState::Gap &&
                 contains_reason(line, sdr_core::SweepLineGapReason::Cancellation));
        }
        if (!saw_cancellation_gap || cancelled.metrics().terminal_control_gaps == 0U ||
            cancelled.metrics().expected_cancellations != 1U || cancelled.metrics().has_error) return 4;

        // The real coordinator must publish before the final segment arrives.
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "100");
        sdr_pluto::ContinuousSweepCoordinator progressive("usb:mock");
        progressive.configure(coordinator_config());
        progressive.start();
        std::optional<sdr_core::SweepProgressFrame> early;
        const auto early_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (!early && std::chrono::steady_clock::now() < early_deadline) {
            early = progressive.poll_progress();
            if (!early) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        const auto completed_before_stop = progressive.metrics().completed_lines;
        progressive.stop();
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "");
        const auto stopped_lines = progressive.poll_lines(0);
        if (!early || early->revision != 1 || completed_before_stop != 0 ||
            early->acquired_segments.size() != 1 ||
            early->acquired_segments[0].config_generation == 0 ||
            !std::isfinite(early->values->front()) || !std::isnan(early->values->back()) ||
            stopped_lines.size() != 1 ||
            !contains_reason(stopped_lines[0], sdr_core::SweepLineGapReason::Cancellation) ||
            !std::isfinite(stopped_lines[0].values->front()) ||
            !std::isnan(stopped_lines[0].values->back()) || progressive.poll_progress()) {
            std::cerr << "progressive publication/cancel coverage contract failed" << std::endl;
            return 28;
        }

        // Reusing the owner creates a new epoch; neither output channel may
        // expose the previous epoch, while a retained immutable preview stays valid.
        auto restarted_config = coordinator_config();
        restarted_config.epoch = early->epoch + 1;
        progressive.configure(restarted_config);
        if (progressive.poll_progress() || !progressive.poll_lines(0).empty()) return 29;
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "100");
        progressive.start();
        std::optional<sdr_core::SweepProgressFrame> restarted_preview;
        const auto restart_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (!restarted_preview && std::chrono::steady_clock::now() < restart_deadline) {
            restarted_preview = progressive.poll_progress();
            if (!restarted_preview) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        progressive.stop();
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "");
        if (!restarted_preview || restarted_preview->epoch != restarted_config.epoch ||
            restarted_preview->revision != 1 || early->epoch == restarted_preview->epoch ||
            !std::isfinite(early->values->front()) || !std::isnan(early->values->back())) {
            std::cerr << "progressive restart epoch isolation failed" << std::endl;
            return 30;
        }

        // A rejected control transition is terminal for this epoch. The
        // coordinator must publish its gap and latch rather than spin in an
        // unbounded hidden reconfigure retry.
        _putenv_s("SDR_MOCK_LIBIIO_GAIN_MODE_READ_FAIL", "1");
        sdr_pluto::ContinuousSweepCoordinator failed_reconfigure("usb:mock");
        auto failing_config = coordinator_config();
        for (auto& segment : failing_config.segments) {
            segment.fixed_band.device.gain_mode = sdr_core::GainMode::SlowAttack;
        }
        failed_reconfigure.configure(failing_config);
        failed_reconfigure.start();
        const auto failure_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (std::chrono::steady_clock::now() < failure_deadline &&
               failed_reconfigure.state() != sdr_core::EngineState::Error) {
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
        failed_reconfigure.join();
        _putenv_s("SDR_MOCK_LIBIIO_GAIN_MODE_READ_FAIL", "");
        const auto failed_lines = failed_reconfigure.poll_lines(0U);
        if (failed_reconfigure.state() != sdr_core::EngineState::Error ||
            failed_reconfigure.metrics().segment_reconfigurations != 0U ||
            failed_reconfigure.metrics().terminal_control_gaps != 1U ||
            failed_lines.size() != 1U ||
            !contains_reason(failed_lines.front(), sdr_core::SweepLineGapReason::Reconfigure)) return 5;

        // Fail only the second control transition: the first segment must
        // survive as measured coverage, not become an empty planned gap.
        _putenv_s("SDR_MOCK_LIBIIO_LO_WRITE_FAIL_AT_HZ", "2450500000");
        sdr_pluto::ContinuousSweepCoordinator prefix_failure("usb:mock");
        auto prefix_config = coordinator_config();
        prefix_failure.configure(prefix_config);
        prefix_failure.start();
        const auto prefix_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (std::chrono::steady_clock::now() < prefix_deadline &&
               prefix_failure.state() != sdr_core::EngineState::Error) {
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
        prefix_failure.stop();
        _putenv_s("SDR_MOCK_LIBIIO_LO_WRITE_FAIL_AT_HZ", "");
        const auto prefix_lines = prefix_failure.poll_lines(0U);
        if (prefix_lines.size() != 1 ||
            !contains_reason(prefix_lines[0], sdr_core::SweepLineGapReason::Reconfigure) ||
            prefix_lines[0].missing_segment_indices != std::vector<std::uint32_t>{1} ||
            prefix_lines[0].segment_generations[0].config_generation == 0 ||
            !std::isfinite(prefix_lines[0].values->front()) ||
            !std::isnan(prefix_lines[0].values->back())) {
            std::cerr << "second-segment failure discarded acquired coverage" << std::endl;
            return 27;
        }

        // An uncovered display span is not silently completed by a retune or
        // GUI-side interpolation.
        auto uncovered = coordinator_config();
        uncovered.segments[1].usable_start_hz = 2'450'300'000.0;
        bool rejected = false;
        try {
            sdr_pluto::validate(uncovered);
        } catch (const sdr_core::ConfigurationError&) {
            rejected = true;
        }
        if (!rejected) return 6;
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl;
        return 99;
    }
    return 0;
}
