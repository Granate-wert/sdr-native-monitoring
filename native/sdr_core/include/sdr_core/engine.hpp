#pragma once

#include "sdr_core/bounded_queue.hpp"
#include "sdr_core/buffer_pool.hpp"
#include "sdr_core/configuration.hpp"
#include "sdr_core/events.hpp"
#include "sdr_core/metrics.hpp"
#include "sdr_core/stop_token.hpp"
#include "sdr_core/synthetic_source.hpp"
#include "sdr_core/types.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

namespace sdr_core {

// Immutable engine configuration. Every capacity is a hard bound; the
// engine never grows queues, pools or backlog buffers at runtime.
struct EngineConfig {
    std::uint32_t acquisition_queue_capacity{64U};
    OverflowPolicy acquisition_overflow{OverflowPolicy::DropNewest};
    std::uint32_t dsp_queue_capacity{64U};
    OverflowPolicy dsp_overflow{OverflowPolicy::DropNewest};
    std::uint32_t snapshot_queue_capacity{4U};
    std::uint32_t event_queue_capacity{64U};
    std::uint32_t recorder_queue_capacity{8U};
    OverflowPolicy recorder_overflow{OverflowPolicy::DropNewest};
    bool recorder_stop_on_overflow{false};
    std::uint32_t pool_block_count{128U};
    std::uint32_t block_size_samples{4096U};
    double sample_rate_hz{1'024'000.0};
    double center_frequency_hz{100'000'000.0};
    std::uint32_t blocks_per_second{0U};  // 0 = producer not rate limited
    bool recorder_enabled{false};
    std::uint64_t max_blocks{0U};  // 0 = run until request_stop()
    SyntheticScenario scenario{SyntheticScenario::BroadbandNoise};
    std::uint64_t seed{0x5344525F50303401ULL};
    std::uint32_t snapshot_interval_blocks{64U};
    // P05: CPU DSP stage configuration and spectrum output bound.
    DspConfig dsp{
        .fft_size = 1024U,
        .hop_size = 1024U,
    };
    std::uint32_t spectrum_queue_capacity{8U};
    bool dc_removal_block_mean{false};
    std::uint32_t schema_version{contract_schema_version};
};

void validate(const EngineConfig& value);

enum class QueueId : std::uint8_t {
    Acquisition,
    Dsp,
    Recorder,
    Snapshot,
    Event,
    Spectrum,
};

// Native transport/DSP engine: deterministic producer -> bounded acquisition
// ring -> mover -> bounded DSP queue -> continuous P05 CPU FFT consumer, with
// an optional recorder tee. No device SDK or Python dependency: the engine is
// a pure C++ data plane.
//
// Lifecycle:
//   CREATED -> CONFIGURED -> RUNNING -> STOPPING -> STOPPED
//                              \-> ERROR (worker failure)
// configure() is valid in CREATED/CONFIGURED/STOPPED and increments the
// configuration generation. start() requires CONFIGURED. request_stop()
// requires RUNNING, unblocks every wait and is idempotent. join() requires
// STOPPING or ERROR, joins all workers, accounts abandoned items explicitly
// and ends in STOPPED. Any invalid transition throws ConfigurationError.
//
// The destructor performs request_stop()+join() internally and never
// throws; no thread outlives the engine.
class SyntheticEngine final {
public:
    SyntheticEngine();
    ~SyntheticEngine() noexcept;

    SyntheticEngine(const SyntheticEngine&) = delete;
    SyntheticEngine& operator=(const SyntheticEngine&) = delete;

    void configure(const EngineConfig& config);
    void start();
    void request_stop();
    void join();
    void stop();  // request_stop() + join()

    [[nodiscard]] EngineState state() const noexcept;
    [[nodiscard]] std::uint64_t config_generation() const noexcept;
    [[nodiscard]] EngineConfig config() const;

    // Lock-free cumulative counters plus queue depths sampled under each
    // queue's own mutex. Never blocks a worker on the caller's behalf.
    [[nodiscard]] EngineMetrics metrics() const;

    // Coarse-grained, non-blocking drains. max_items == 0 drains everything
    // currently queued.
    [[nodiscard]] std::vector<DiagnosticEvent> poll_events(std::size_t max_items);
    [[nodiscard]] std::vector<EngineMetrics> poll_snapshots(std::size_t max_items);
    // P05: latest-wins SpectrumFrame stream produced by the CPU DSP stage.
    [[nodiscard]] std::vector<SpectrumFrame> poll_spectrum_frames(std::size_t max_items);

    // Consistent per-queue statistics for diagnostics and tests.
    [[nodiscard]] QueueStats queue_stats(QueueId id) const;
    [[nodiscard]] PoolStats pool_stats() const;

private:
    void initiate_shutdown() noexcept;  // worker-safe, idempotent, never throws
    void mark_error() noexcept;  // worker failure: RUNNING/STOPPING -> ERROR, terminal-safe
    void emit_event(EventSeverity severity, std::string code, std::string message) noexcept;

    void producer_run() noexcept;
    void mover_run() noexcept;
    void consumer_run() noexcept;
    void recorder_run() noexcept;

    [[nodiscard]] EngineMetrics assemble_metrics() const;
    void account_abandoned() noexcept;

    mutable std::mutex lifecycle_mutex_;
    std::atomic<EngineState> state_{EngineState::Created};
    std::uint64_t config_generation_{};
    EngineConfig config_{};
    bool configured_{false};

    StopToken stop_{make_stop_token()};
    std::unique_ptr<BoundedQueue<IqBlock>> acquisition_queue_;
    std::unique_ptr<BoundedQueue<IqBlock>> dsp_queue_;
    std::unique_ptr<BoundedQueue<IqBlock>> recorder_queue_;
    std::unique_ptr<BoundedQueue<EngineMetrics>> snapshot_queue_;
    std::unique_ptr<BoundedQueue<DiagnosticEvent>> event_queue_;
    std::unique_ptr<BoundedQueue<SpectrumFrame>> spectrum_queue_;
    std::unique_ptr<BufferPool> pool_;

    EngineMetricsCounters counters_{};
    std::atomic<std::uint64_t> events_lost_{};
    std::atomic<std::uint64_t> event_sequence_{};
    std::uint64_t events_lost_reported_{};  // guarded by lifecycle_mutex_

    std::thread producer_thread_;
    std::thread mover_thread_;
    std::thread consumer_thread_;
    std::thread recorder_thread_;
};

}  // namespace sdr_core
