#pragma once

#include "sdr_core/sweep_line_assembler.hpp"

#include <cstddef>
#include <cstdint>
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
    // Power-major counts. Probability at (power, f) is count / observations[f].
    // Zero observations means unavailable, never a measured zero probability.
    SharedArray<std::uint32_t> histogram_counts;
    SharedArray<std::uint32_t> observations;
};

// Single-owner native kernel. No Qt, device, timer, publication queue or DSP
// reprocessing. The coordinator must feed it BEFORE display coalescing.
// Identity/grid are fixed for its lifetime; a new epoch requires a new kernel.
// Not yet wired into the product coordinator or Python render contract.
class SweepStatisticsAccumulator final {
public:
    SweepStatisticsAccumulator(
        SweepStatisticsConfig config,
        SourceDescriptor source,
        std::uint64_t epoch,
        SpectrumUnit unit,
        SharedArray<double> frequencies_hz
    );

    // Includes owned array payload + one live output snapshot, not allocator
    // overhead, caller-owned input frames or additional retained snapshots.
    // Throws before allocation on overflow/invalid configuration/over budget.
    [[nodiscard]] static std::size_t required_payload_bytes(
        const SweepStatisticsConfig& config, std::size_t frequency_bins
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
    std::vector<double> power_sum_;
    std::vector<double> power_correction_;
    std::size_t next_slot_{};
    std::uint32_t retained_{};
    std::uint64_t newest_sequence_{};
    std::uint64_t unique_passes_{};
    std::uint64_t updates_{};
};

}  // namespace sdr_core
