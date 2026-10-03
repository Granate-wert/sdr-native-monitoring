#pragma once

#include "sdr_core/bounded_queue.hpp"
#include "sdr_core/types.hpp"
#include "sdr_core/sweep_line_assembler.hpp"
#include "sdr_pluto/fixed_band_engine.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
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

// Two explicit views of ONE hardware plan. The second plan must not request
// independent tuning. Resource identity is supplied by the admitted owner,
// never parsed from a producer name. Not yet a product/lease admission API.
struct PairedContinuousSweepCoordinatorConfig {
    std::string resource_id;
    ContinuousSweepCoordinatorConfig primary;
    ContinuousSweepCoordinatorConfig secondary;
};

struct PairedSweepStepReceipt {
    std::uint32_t step_index{};
    std::uint64_t config_generation{};
    std::uint64_t synchronization_epoch{};
    std::uint64_t shared_input_gaps_before{};
    std::uint64_t frame_sequence{};
    std::uint64_t first_sample_index{};
    std::int64_t timestamp_ns{}; // Retained producer time, not invented RF time.
    double center_frequency_hz{};
    double sample_rate_hz{};
    double analog_bandwidth_hz{};
    std::uint32_t fft_size{};
};

struct PairedSweepLineFrame {
    std::string resource_id;
    sdr_core::SweepLineFrame primary;
    sdr_core::SweepLineFrame secondary;
    std::vector<PairedSweepStepReceipt> steps;
};

struct PairedSweepProgressFrame {
    std::string resource_id;
    sdr_core::SweepProgressFrame primary;
    sdr_core::SweepProgressFrame secondary;
    std::vector<PairedSweepStepReceipt> steps;
};

void validate(const PairedContinuousSweepCoordinatorConfig& value);

// Multi-segment coordinator wall-clock stages, not RF dwell or ADC timestamps.
// A live metrics() call is an independent relaxed snapshot of these counters;
// a post-Stop snapshot is required for exact count/total consistency.
struct ContinuousSweepStageTiming {
    std::uint64_t count{};
    std::uint64_t total_ns{};
    std::uint64_t max_ns{};
};

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
    // Stop/configure/start are successful lifecycle calls for multi-segment
    // Sweep. Frame wait includes polling for the current-generation FFT.
    // These counters do not change the device, DSP or publication policy.
    ContinuousSweepStageTiming segment_stop_timing;
    ContinuousSweepStageTiming segment_configure_timing;
    ContinuousSweepStageTiming segment_start_timing;
    ContinuousSweepStageTiming segment_frame_wait_timing;
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
    // In paired mode the legacy analytical count above is RX1 only, NOT two
    // streams summed. This separate RX2 count excludes repeated common IQ.
    std::uint64_t secondary_analytical_fft_frames{};
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
    explicit ContinuousSweepCoordinator(std::string uri, std::uint32_t timeout_ms = 3000U,
                                        std::optional<std::string> expected_serial = std::nullopt);
    ~ContinuousSweepCoordinator() noexcept;

    ContinuousSweepCoordinator(const ContinuousSweepCoordinator&) = delete;
    ContinuousSweepCoordinator& operator=(const ContinuousSweepCoordinator&) = delete;

    void configure(ContinuousSweepCoordinatorConfig config);
    void configure_paired(PairedContinuousSweepCoordinatorConfig config);
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
    // Drain at most the entry queue depth; zero means that finite entry bound.
    [[nodiscard]] std::vector<sdr_core::SweepLineFrame> poll_lines(std::size_t max_items);
    [[nodiscard]] std::optional<sdr_core::SweepProgressFrame> poll_progress();
    [[nodiscard]] std::vector<PairedSweepLineFrame> poll_paired_lines(std::size_t max_items);
    [[nodiscard]] std::optional<PairedSweepProgressFrame> poll_paired_progress();
    // Native paired first-cause diagnostic. Legacy single failure reporting
    // remains in its metrics; this string is not an RF validity claim.
    [[nodiscard]] std::string last_error() const;
    // Native test-only seams, deliberately not Python/product controls.
    void set_start_delay_for_test(std::uint32_t milliseconds);
    [[nodiscard]] bool start_pending_for_test() const noexcept;
    void set_secondary_nan_step_for_test(std::int32_t step_index);
    // Native-only queue drain for benchmark/evidence consumers. It releases
    // completed line buffers without materialising their spectrum arrays at
    // the Python boundary.
    [[nodiscard]] std::size_t discard_lines(std::size_t max_items);

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_pluto
