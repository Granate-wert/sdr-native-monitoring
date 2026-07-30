#pragma once

#include "sdr_core/bounded_queue.hpp"
#include "sdr_core/configuration.hpp"
#include "sdr_core/events.hpp"
#include "sdr_core/metrics.hpp"
#include "sdr_core/persistence.hpp"
#include "sdr_core/types.hpp"
#include "sdr_pluto/pluto_backend.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace sdr_pluto {

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
    // P08 compute backend selection. AUTO uses CUDA only after self-test and
    // above the measured workload crossover; see ADR-022.
    sdr_core::ComputeBackendKind backend{sdr_core::ComputeBackendKind::Auto};
    bool allow_runtime_fallback{true};
    std::uint32_t acquisition_queue_capacity{16U};
    sdr_core::OverflowPolicy acquisition_overflow{sdr_core::OverflowPolicy::DropNewest};
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
    sdr_core::QueueStats spectrum_queue;
    sdr_core::QueueStats persistence_queue;
    std::uint64_t transient_blocks_discarded{};
    std::uint64_t transient_samples_discarded{};
    std::uint64_t spectrum_snapshots_superseded{};
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
#endif
private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_pluto
