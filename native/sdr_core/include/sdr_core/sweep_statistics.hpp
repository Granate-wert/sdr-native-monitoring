#pragma once

#include "sdr_core/sweep_line_assembler.hpp"

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <vector>

namespace sdr_core {

// Exact rolling statistics over unique passes, not UI publications or FFTs.
// Missing (NaN) bins contribute neither power nor a histogram observation.
struct SweepStatisticsConfig {
    std::uint32_t window_passes{};
    std::uint32_t power_bins{};
    double power_min_db{};
    double power_max_db{};
    std::size_t max_payload_bytes{};
    // Zero retains exact per-bin density. Nonzero pools native bin observations
    // into regular frequency cells; it does NOT reduce the average/FFT grid.
    std::uint32_t density_columns{};
};

struct SweepStatisticsSnapshot {
    SourceType source_type{SourceType::Synthetic};
    std::string source_id;
    std::uint64_t epoch{};
    std::uint64_t update_sequence{};
    std::uint64_t newest_pass_sequence{};
    std::uint64_t unique_passes_seen{};
    std::uint32_t retained_passes{};
    SpectrumUnit unit{SpectrumUnit::DbfsBin};
    double power_min_db{};
    double power_max_db{};
    std::uint32_t power_bins{};
    SharedArray<double> frequencies_hz;
    SharedArray<float> average_db;
    // Includes finite observations from partial AND terminal-gap passes.
    // Power-major counts. Probability uses density_observations per cell.
    // Zero observations means unavailable, never a measured zero probability.
    SharedArray<std::uint32_t> histogram_counts;
    SharedArray<std::uint32_t> observations;
    SharedArray<double> density_frequency_edges_hz;
    SharedArray<std::uint32_t> density_observations;
    SharedArray<float> probability;
};

// Single-owner native kernel. No Qt, device, timer, publication queue or DSP
// reprocessing. The coordinator must feed it BEFORE display coalescing.
// Identity/grid are fixed for its lifetime; a new epoch requires a new kernel.
// The native coordinator is wired; Python render-contract exposure is separate.
class SweepStatisticsAccumulator final {
public:
    SweepStatisticsAccumulator(
        SweepStatisticsConfig config,
        SourceDescriptor source,
        std::uint64_t epoch,
        SpectrumUnit unit,
        SharedArray<double> frequencies_hz
    );
    SweepStatisticsAccumulator(const SweepStatisticsAccumulator&) = delete;
    SweepStatisticsAccumulator& operator=(const SweepStatisticsAccumulator&) = delete;

    // Includes owned array payload + bounded retained output snapshots, not allocator
    // overhead, caller-owned input frames or additional retained snapshots.
    // Throws before allocation on overflow/invalid configuration/over budget.
    [[nodiscard]] static std::size_t required_payload_bytes(
        const SweepStatisticsConfig& config, std::size_t frequency_bins,
        std::size_t retained_snapshot_slots = 1
    );

    // False for duplicates/stale revisions/closed or evicted passes. Terminal
    // replaces the same pass even if a later pass is already being acquired.
    [[nodiscard]] bool update(const SweepProgressFrame& frame);
    [[nodiscard]] bool update(const SweepLineFrame& frame);
    [[nodiscard]] SweepStatisticsSnapshot snapshot() const;
    // Explicit new run only; allows sequence reuse without inventing RF time.
    void reset() noexcept;

private:
    struct Pass {
        std::uint64_t sequence{};
        std::uint64_t revision{};
        bool occupied{};
        bool terminal{};
    };

    [[nodiscard]] bool admit(
        const SourceDescriptor& source, std::uint64_t epoch, SpectrumUnit unit,
        const SharedArray<double>& frequencies, const SharedArray<float>& values,
        std::uint64_t sequence, std::uint64_t revision, bool terminal
    );
    [[nodiscard]] std::size_t power_bin(float value) const noexcept;
    [[nodiscard]] std::size_t density_column(std::size_t frequency) const noexcept;
    void add_power(std::size_t frequency, double value) noexcept;

    SweepStatisticsConfig config_;
    SourceDescriptor source_;
    std::uint64_t epoch_{};
    SpectrumUnit unit_;
    SharedArray<double> frequencies_;
    std::vector<Pass> passes_;
    std::vector<float> values_;
    std::vector<std::uint32_t> histogram_;
    std::vector<std::uint32_t> observations_;
    std::vector<std::uint32_t> density_observations_;
    SharedArray<double> density_edges_;
    std::vector<double> power_sum_;
    std::vector<double> power_correction_;
    std::size_t next_slot_{};
    std::uint32_t retained_{};
    std::uint64_t newest_sequence_{};
    std::uint64_t unique_passes_{};
    std::uint64_t updates_{};
};

// Shared native owner used by the DSP-thread one-window path and coordinator
// multi-retune path. Consumes measurements before lossy publication queues.
// A caller-supplied steady-clock value controls only snapshot cost, not RF time.
class SweepStatisticsPublisher final {
public:
    SweepStatisticsPublisher(SweepStatisticsConfig config, SourceDescriptor source,
        std::uint64_t epoch, SpectrumUnit unit, SharedArray<double> frequencies,
        double snapshot_rate_hz, std::size_t retained_snapshot_slots);
    void consume(SweepProgressFrame& frame, std::int64_t steady_ns);
    void consume(SweepLineFrame& frame, std::int64_t steady_ns, bool force_snapshot = false);
    [[nodiscard]] std::uint64_t newest_sequence() const;
    [[nodiscard]] std::size_t payload_bytes() const noexcept { return payload_bytes_; }

private:
    void refresh(std::int64_t steady_ns, bool force);
    std::size_t payload_bytes_{};
    SweepStatisticsAccumulator accumulator_;
    std::int64_t period_ns_{};
    std::int64_t last_snapshot_ns_{};
    std::uint64_t newest_sequence_{};
    std::shared_ptr<const SweepStatisticsSnapshot> latest_;
    mutable std::mutex mutex_;
};

}  // namespace sdr_core
