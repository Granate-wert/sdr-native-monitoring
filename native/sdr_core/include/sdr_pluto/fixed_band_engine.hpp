#pragma once

#include "sdr_core/bounded_queue.hpp"
#include "sdr_core/configuration.hpp"
#include "sdr_core/events.hpp"
#include "sdr_core/metrics.hpp"
#include "sdr_core/persistence.hpp"
#include "sdr_core/types.hpp"
#include "sdr_core/sweep_statistics.hpp"
#include "sdr_pluto/pluto_backend.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace sdr_pluto {

// R10-D1A single-window max-rate Sweep profile.  It turns every admitted
// native SpectrumFrame into a completed reduced line over the requested
// display span.  The span must fit inside the explicitly declared usable
// receiver window, so this profile never retunes merely to imitate Sweep.
// It deliberately excludes raw I/Q recording; capture is an RTBW-only path.
struct ContinuousSweepLineConfig {
    bool enabled{};
    std::uint64_t epoch{};
    double display_start_hz{};
    double display_stop_hz{};
    double usable_window_hz{};
    std::uint32_t output_queue_capacity{4U};
    // When nonzero, this is the final reduced analysis-grid N for one usable
    // RF window. The fixed-band DSP transform remains power-of-two and must
    // have at least this density before the line assembler is allowed to
    // interpolate down onto the analysis grid.
    std::uint32_t analysis_bins_per_usable_window{};
    // Rate of completed reduced lines, paced by native FFT timestamps. It is
    // separate from the Qt/render cadence and bounds native line assembly.
    // Evidence may select a higher finite cadence without raising Render FPS.
    double line_snapshot_rate_hz{60.0};
};

void validate(const ContinuousSweepLineConfig& value);

// P07 fixed-band configuration. Every queue capacity is a hard memory bound.
// The native data plane processes every admitted I/Q block; snapshot_rate_hz
// limits only the Python/render boundary.
struct FixedBandConfig {
    sdr_core::DeviceConfig device;
    sdr_core::DspConfig dsp{
        .fft_size = 4096U,
        .hop_size = 2048U,
    };
    sdr_core::PersistenceConfig persistence{};
    // R08-B native durable writer configuration. It is independent from the
    // legacy Python recording service and may not route raw I/Q or published
    // spectrum frames through Python/Qt.
    sdr_core::RecordingConfig recording{};
    // Provisional native-only R10-D1A profile.  Python/Qt presentation is
    // intentionally added only after its bounded C++ path is verified.
    std::optional<ContinuousSweepLineConfig> continuous_sweep_line;
    // Native-only coordinator sink, intentionally not a Python callback/field.
    // Consumed by the DSP owner before the bounded single-window relay.
    std::shared_ptr<sdr_core::SweepStatisticsPublisher> sweep_statistics_sink;
    // P08 compute backend selection. AUTO uses CUDA only after self-test and
    // above the measured workload crossover; see ADR-022.
    sdr_core::ComputeBackendKind backend{sdr_core::ComputeBackendKind::Auto};
    bool allow_runtime_fallback{true};
    std::uint32_t acquisition_queue_capacity{16U};
    sdr_core::OverflowPolicy acquisition_overflow{sdr_core::OverflowPolicy::DropNewest};
    // R08-A: a native pre-DSP recorder tee.  The queue retains shared I/Q
    // buffers only; this phase deliberately has no Python drain or disk
    // writer, so a slow or absent recorder can never stall the DSP path.
    bool recorder_enabled{false};
    std::uint32_t recorder_queue_capacity{8U};
    sdr_core::OverflowPolicy recorder_overflow{sdr_core::OverflowPolicy::DropNewest};
    std::uint32_t spectrum_queue_capacity{4U};
    std::uint32_t event_queue_capacity{64U};
    double snapshot_rate_hz{60.0};
    std::uint32_t discard_blocks_after_start{2U};
    bool dc_removal_block_mean{false};
    std::uint32_t schema_version{sdr_core::contract_schema_version};
};

void validate(const FixedBandConfig& value);

// One coherent diagnostic snapshot. Snapshot queue loss is deliberately
// separate from analytical FFT loss: a slow Python poller may supersede
// render snapshots without discarding an FFT from the native DSP pipeline.
struct FixedBandMetrics {
    sdr_core::EngineState state{sdr_core::EngineState::Created};
    bool has_error{};
    sdr_core::EngineMetrics engine;
    StreamMetrics device;
    sdr_core::QueueStats acquisition_queue;
    // R08-A recorder queue counters are intentionally separate from source
    // and analytical loss.  A recorder drop means the I/Q capture is
    // incomplete, but must never be reported as an FFT/DSP drop.
    sdr_core::QueueStats recorder_queue;
    sdr_core::QueueStats spectrum_queue;
    sdr_core::QueueStats sweep_line_queue;
    sdr_core::QueueStats spectrum_recorder_queue;
    sdr_core::QueueStats persistence_queue;
    // Loss taxonomy is intentionally not folded into the backward-compatible
    // EngineMetrics::iq_*_dropped totals. These counters identify only I/Q
    // blocks rejected or evicted by the bounded acquisition queue; source
    // loss stays in device, DSP loss in engine.fft_frames_dropped, and
    // publication supersession below.
    std::uint64_t acquisition_queue_blocks_dropped{};
    std::uint64_t acquisition_queue_samples_dropped{};
    // Host-boundary continuity checks over consecutive IqBlock metadata.
    // These do not substitute for a device/FPGA hardware-overflow counter.
    std::uint64_t source_sequence_discontinuities{};
    std::uint64_t source_sample_index_discontinuities{};
    std::uint64_t source_timestamp_regressions{};
    std::uint64_t source_estimated_timestamp_blocks{};
    bool hardware_overflow_counter_available{};
    std::uint64_t recorder_queue_blocks_dropped{};
    std::uint64_t recorder_queue_samples_dropped{};
    std::uint64_t recorder_shutdown_blocks_discarded{};
    std::uint64_t recorder_shutdown_samples_discarded{};
    std::uint64_t recorder_writer_blocks_written{};
    std::uint64_t recorder_writer_samples_written{};
    std::uint64_t recorder_writer_bytes_written{};
    std::uint64_t recorder_writer_blocks_unavailable{};
    std::uint64_t recorder_writer_samples_unavailable{};
    bool recorder_writer_failed{};
    std::uint64_t spectrum_recorder_frames_dropped{};
    std::uint64_t spectrum_recorder_frames_unavailable{};
    std::uint64_t spectrum_recorder_shutdown_frames_discarded{};
    std::uint64_t spectrum_writer_frames_written{};
    std::uint64_t spectrum_writer_bytes_written{};
    bool spectrum_writer_failed{};
    std::uint64_t transient_blocks_discarded{};
    std::uint64_t transient_samples_discarded{};
    std::uint64_t spectrum_snapshots_superseded{};
    std::uint64_t sweep_line_snapshots_superseded{};
    std::uint64_t completed_sweep_lines{};
    std::uint64_t gapped_sweep_lines{};
    std::uint64_t sweep_line_capacity_evicted{};
    std::uint64_t persistence_snapshots_superseded{};
    std::uint64_t shutdown_blocks_discarded{};
    std::uint64_t shutdown_samples_discarded{};
    std::uint64_t expected_cancellations{};
    std::uint64_t diagnostic_events_lost{};
    // P08 backend visibility (§8.2): requested vs actual backend and the
    // fallback counters of the DSP stage.
    sdr_core::ComputeBackendKind requested_backend{sdr_core::ComputeBackendKind::Auto};
    sdr_core::ComputeBackendKind active_backend{sdr_core::ComputeBackendKind::Cpu};
    bool backend_self_test_passed{};
    std::uint64_t backend_fallback_count{};
    std::uint64_t backend_switch_count{};
    sdr_core::BackendErrorCode last_backend_error{sdr_core::BackendErrorCode::None};
};

// One bounded native-to-Python bridge read.  It never represents analytical
// FFT loss: `coalesced_frames` are already-published SpectrumFrames removed
// from the fixed-capacity presentation queue because this caller requested the
// newest currently available frame.  The returned frame keeps its canonical
// source, sequence, timestamp, generation and all existing loss metadata.
struct LatestSpectrumFrameDrain {
    std::optional<sdr_core::SpectrumFrame> frame;
    std::uint32_t coalesced_frames{};
};

// Windows Pluto/libiio acquisition -> bounded native queue -> CPU DSP ->
// bounded latest-wins SpectrumFrame engine. No Python callback participates
// in the high-rate path.
class FixedBandEngine final {
public:
    explicit FixedBandEngine(std::string uri, std::uint32_t timeout_ms = 3000U);
    ~FixedBandEngine() noexcept;

    FixedBandEngine(const FixedBandEngine&) = delete;
    FixedBandEngine& operator=(const FixedBandEngine&) = delete;
    FixedBandEngine(FixedBandEngine&&) = delete;
    FixedBandEngine& operator=(FixedBandEngine&&) = delete;

    [[nodiscard]] AppliedConfig configure(const FixedBandConfig& config);
    // Stop -> apply/readback -> reset DSP/queues -> optional resume.
    [[nodiscard]] AppliedConfig reconfigure(const FixedBandConfig& config);
    void start();
    void request_stop();
    void join();
    void stop();
    void disconnect() noexcept;

    [[nodiscard]] bool connected() const noexcept;
    [[nodiscard]] bool streaming() const noexcept;
    [[nodiscard]] sdr_core::EngineState state() const noexcept;
    [[nodiscard]] std::uint64_t config_generation() const noexcept;
    [[nodiscard]] FixedBandConfig config() const;
    [[nodiscard]] AppliedConfig applied_config() const;
    [[nodiscard]] FixedBandMetrics metrics() const;

    [[nodiscard]] std::vector<sdr_core::SpectrumFrame> poll_spectrum_frames(
        std::size_t max_items
    );
    // Atomically drains the existing bounded/latest-wins SpectrumFrame queue
    // and returns at most its newest frame.  This is the UI bridge path: it
    // avoids materialising discarded SpectrumFrames in Python while retaining
    // exact, separate coalescing accounting. It must not be used by native
    // Sweep-line assembly or native Spectrum recording, which run upstream.
    [[nodiscard]] LatestSpectrumFrameDrain drain_latest_spectrum_frame();
    // R10-D1A low-rate reduced-spectrum boundary.  It never returns raw I/Q
    // and uses a bounded latest-wins queue independent of render cadence.
    [[nodiscard]] std::vector<sdr_core::SweepLineFrame> poll_sweep_line_frames(
        std::size_t max_items
    );
    // Native-only staging drain for the future binary writer.  It is not
    // exposed through pybind: handing every raw block to Python would violate
    // the live data-plane boundary.  R08-B replaces this with a native writer.
    [[nodiscard]] std::vector<sdr_core::IqBlock> poll_recorded_iq_blocks(
        std::size_t max_items
    );
    [[nodiscard]] std::vector<sdr_core::PersistenceSnapshot> poll_persistence_snapshots(
        std::size_t max_items
    );
    [[nodiscard]] std::vector<sdr_core::DiagnosticEvent> poll_events(
        std::size_t max_items
    );

#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
    // Deterministic diagnostic overflow testing; absent from production builds.
    void emit_diagnostic_for_test(
        sdr_core::EventSeverity severity,
        std::string code,
        std::string message
    );
    // Test-only DSP pacing makes acquisition-overflow accounting independent
    // from host CPU speed. It is not present in production builds or pybind.
    void set_dsp_delay_for_test(std::uint32_t milliseconds) noexcept;
#endif
private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_pluto
