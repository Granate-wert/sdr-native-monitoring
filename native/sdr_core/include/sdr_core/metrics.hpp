#pragma once

#include <atomic>
#include <cstdint>

namespace sdr_core {

struct EngineMetrics {
    std::uint64_t iq_samples_received{};
    std::uint64_t iq_samples_dropped{};
    std::uint64_t iq_blocks_received{};
    std::uint64_t iq_blocks_dropped{};
    std::uint64_t fft_frames_computed{};
    std::uint64_t fft_frames_dropped{};
    double analytical_fft_rate{};
    std::uint64_t spectrum_snapshots_emitted{};
    std::uint64_t waterfall_rows_emitted{};
    std::uint64_t persistence_updates{};
    std::uint64_t render_snapshots_applied{};
    std::uint32_t acquisition_queue_depth{};
    std::uint32_t dsp_queue_depth{};
    std::uint32_t recorder_queue_depth{};
    double cpu_processing_ms{};
    double gpu_processing_ms{};
    double h2d_ms{};
    double d2h_ms{};
    double end_to_end_latency_ms{};
};

void validate(const EngineMetrics& value);

// Lock-free cumulative counters backing EngineMetrics. Producers/consumers
// increment with relaxed ordering; snapshot() reads every counter without
// taking a lock, so concurrent reads never block the data plane. Queue
// depths are not stored here: they are sampled from the bounded queues by
// the engine when a metrics snapshot is assembled.
//
// Counters are cache-line aligned to avoid false sharing between producer
// and consumer threads (MSVC C4324 padding is intentional).
#if defined(_MSC_VER)
#pragma warning(push)
#pragma warning(disable : 4324)
#endif
struct EngineMetricsCounters {
    alignas(64) std::atomic<std::uint64_t> iq_samples_received{};
    alignas(64) std::atomic<std::uint64_t> iq_samples_dropped{};
    alignas(64) std::atomic<std::uint64_t> iq_blocks_received{};
    alignas(64) std::atomic<std::uint64_t> iq_blocks_dropped{};
    alignas(64) std::atomic<std::uint64_t> fft_frames_computed{};
    alignas(64) std::atomic<std::uint64_t> fft_frames_dropped{};
    alignas(64) std::atomic<std::uint64_t> spectrum_snapshots_emitted{};
    alignas(64) std::atomic<double> analytical_fft_rate{};
    alignas(64) std::atomic<double> cpu_processing_ms{};

    EngineMetricsCounters() = default;
    EngineMetricsCounters(const EngineMetricsCounters&) = delete;
    EngineMetricsCounters& operator=(const EngineMetricsCounters&) = delete;

    void reset() noexcept {
        iq_samples_received.store(0U, std::memory_order_relaxed);
        iq_samples_dropped.store(0U, std::memory_order_relaxed);
        iq_blocks_received.store(0U, std::memory_order_relaxed);
        iq_blocks_dropped.store(0U, std::memory_order_relaxed);
        fft_frames_computed.store(0U, std::memory_order_relaxed);
        fft_frames_dropped.store(0U, std::memory_order_relaxed);
        spectrum_snapshots_emitted.store(0U, std::memory_order_relaxed);
        analytical_fft_rate.store(0.0, std::memory_order_relaxed);
        cpu_processing_ms.store(0.0, std::memory_order_relaxed);
    }

    // Cumulative fields only; queue depths remain zero here.
    [[nodiscard]] EngineMetrics snapshot() const noexcept {
        EngineMetrics result;
        result.iq_samples_received = iq_samples_received.load(std::memory_order_relaxed);
        result.iq_samples_dropped = iq_samples_dropped.load(std::memory_order_relaxed);
        result.iq_blocks_received = iq_blocks_received.load(std::memory_order_relaxed);
        result.iq_blocks_dropped = iq_blocks_dropped.load(std::memory_order_relaxed);
        result.fft_frames_computed = fft_frames_computed.load(std::memory_order_relaxed);
        result.fft_frames_dropped = fft_frames_dropped.load(std::memory_order_relaxed);
        result.spectrum_snapshots_emitted =
            spectrum_snapshots_emitted.load(std::memory_order_relaxed);
        result.analytical_fft_rate = analytical_fft_rate.load(std::memory_order_relaxed);
        result.cpu_processing_ms = cpu_processing_ms.load(std::memory_order_relaxed);
        return result;
    }
};
#if defined(_MSC_VER)
#pragma warning(pop)
#endif

}  // namespace sdr_core
