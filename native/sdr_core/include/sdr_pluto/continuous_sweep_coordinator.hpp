#pragma once

#include "sdr_core/bounded_queue.hpp"
#include "sdr_core/types.hpp"
#include "sdr_core/sweep_line_assembler.hpp"
#include "sdr_pluto/fixed_band_engine.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace sdr_pluto {

// One planned RF epoch of a continuous wideband line. The fixed-band engine
// performs the actual transactional retune/readback; usable bounds are owned
// by the coordinator and are never inferred from a stale spectrum frame.
struct ContinuousSweepSegmentConfig {
    FixedBandConfig fixed_band;
    double usable_start_hz{};
    double usable_stop_hz{};
};

// R10-D1B's native-only multi-segment plan. It owns one FixedBandEngine and
// never delivers raw I/Q or individual native FFT frames to Python/Qt.
struct ContinuousSweepCoordinatorConfig {
    std::uint64_t epoch{};
    double display_start_hz{};
    double display_stop_hz{};
    // Original guard-window declaration, retained independently from a
    // clipped display span. Zero is the legacy compatibility form and means
    // the display span itself; new physical plans must supply it explicitly.
    double usable_window_hz{};
    // User-visible analysis N per usable window. Zero preserves the original
    // physical-FFT-bin spacing behaviour for existing callers.
    std::uint32_t analysis_bins_per_usable_window{};
    // Native completed-line cadence for the one-window post-DSP path. It is
    // deliberately independent from FixedBandConfig::snapshot_rate_hz, whose
    // bounded SpectrumFrame publication may be chosen for a UI consumer.
    // Zero preserves the legacy fallback to the fixed-band snapshot rate.
    double line_snapshot_rate_hz{};
    std::uint32_t output_queue_capacity{4U};
    std::uint32_t segment_frame_timeout_ms{1000U};
    std::vector<ContinuousSweepSegmentConfig> segments;
    std::optional<sdr_core::SweepStatisticsConfig> statistics;
    double statistics_snapshot_rate_hz{15.0};
};

void validate(const ContinuousSweepCoordinatorConfig& value);

struct ContinuousSweepCoordinatorMetrics {
    sdr_core::EngineState state{sdr_core::EngineState::Created};
    bool has_error{};
    sdr_core::QueueStats output_queue;
    std::uint64_t completed_lines{};
    std::uint64_t gapped_lines{};
    // Bounded post-DSP relay between FixedBandEngine and this coordinator.
    // These values are not UI output supersession or analytical FFT loss.
    std::uint64_t line_relay_snapshots_superseded{};
    std::uint32_t line_relay_queue_capacity{};
    std::uint32_t line_relay_queue_high_water{};
    std::uint64_t output_snapshots_superseded{};
    std::uint64_t segment_reconfigurations{};
    std::uint64_t segment_frame_timeouts{};
    std::uint64_t terminal_control_gaps{};
    // A benchmark's explicit stop is itself a visible control boundary. This
    // counter distinguishes its one cancellation gap from a failed segment.
    std::uint64_t expected_cancellations{};
    // Cumulative low-rate evidence counters. They are delta-sampled after
    // every accepted frame of the active configuration generation, so a
    // one-window continuous stream is not double-counted and can be compared
    // with its presentation LPS.
    std::uint64_t device_iq_samples{};
    std::uint64_t device_iq_blocks{};
    std::uint64_t analytical_fft_frames{};
    // Every entry is incremented only after the coordinator receives a
    // SpectrumFrame for the segment's currently applied configuration
    // generation and verifies that it covers the declared usable range. It is
    // scalar provenance for segmented-Sweep evidence; no raw I/Q or FFT arrays
    // leave the native data plane.
    std::vector<std::uint64_t> completed_current_generation_fft_frames;
    // Scalar geometry of the first completed native line. It deliberately
    // excludes SweepLineFrame arrays so physical evidence never routes
    // spectrum bins through Python merely to prove the analysis contract.
    bool completed_line_analysis_geometry_available{};
    double completed_line_analysis_window_hz{};
    std::uint32_t completed_line_analysis_bins_per_usable_window{};
    double completed_line_physical_fft_bin_width_hz{};
    std::uint32_t completed_line_physical_fft_size{};
    std::uint64_t completed_line_analysis_geometry_mismatches{};
    std::uint64_t source_short_reads{};
    std::uint64_t source_refill_errors{};
    std::uint64_t source_output_pool_exhaustions{};
    std::uint64_t source_estimated_dropped_samples{};
    std::uint64_t acquisition_queue_blocks_dropped{};
    std::uint64_t acquisition_queue_samples_dropped{};
    std::uint64_t source_sequence_discontinuities{};
    std::uint64_t source_sample_index_discontinuities{};
    std::uint64_t source_timestamp_regressions{};
    std::uint64_t source_estimated_timestamp_blocks{};
    bool hardware_overflow_counter_available{};
    std::uint64_t fft_frames_dropped{};
    std::uint32_t acquisition_queue_high_water{};
    std::uint32_t spectrum_queue_high_water{};
};

class ContinuousSweepCoordinator final {
public:
    explicit ContinuousSweepCoordinator(std::string uri, std::uint32_t timeout_ms = 3000U);
    ~ContinuousSweepCoordinator() noexcept;

    ContinuousSweepCoordinator(const ContinuousSweepCoordinator&) = delete;
    ContinuousSweepCoordinator& operator=(const ContinuousSweepCoordinator&) = delete;

    void configure(ContinuousSweepCoordinatorConfig config);
    void start();
    void request_stop();
    void join();
    void stop();
    void disconnect() noexcept;

    [[nodiscard]] sdr_core::EngineState state() const noexcept;
    [[nodiscard]] ContinuousSweepCoordinatorMetrics metrics() const;
    // A bounded, ordered snapshot of the transactionally applied
    // configuration for every segment of the last *completed* line. This is
    // evidence metadata, not a streaming data path: no raw I/Q or
    // SpectrumFrame crosses it.
    [[nodiscard]] std::vector<AppliedConfig> applied_segments() const;
    [[nodiscard]] std::vector<sdr_core::SweepLineFrame> poll_lines(std::size_t max_items);
    [[nodiscard]] std::optional<sdr_core::SweepProgressFrame> poll_progress();
    // Native-only queue drain for benchmark/evidence consumers. It releases
    // completed line buffers without materialising their spectrum arrays at
    // the Python boundary.
    [[nodiscard]] std::size_t discard_lines(std::size_t max_items);

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_pluto
