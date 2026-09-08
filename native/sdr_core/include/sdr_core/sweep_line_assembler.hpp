#pragma once

#include "sdr_core/types.hpp"

#include <cstdint>
#include <map>
#include <optional>
#include <vector>

namespace sdr_core {

// Nonterminal publication. Never feed this into terminal line accounting.
// Revision counts accepted segments, not paints or calls to preview().
struct SweepProgressFrame {
    SourceDescriptor source;
    std::uint64_t line_sequence{};
    std::uint64_t epoch{};
    std::uint64_t revision{};
    SpectrumUnit unit{SpectrumUnit::DbfsBin};
    SharedArray<double> frequencies_hz;
    SharedArray<float> values;
    SharedArray<std::uint32_t> quality_flags_per_bin;
    SharedArray<std::int32_t> source_segment_indices;
    std::vector<SweepLineSegmentDefinition> acquired_segments;
    std::vector<std::uint32_t> pending_segment_indices;
    std::vector<SweepSegmentAcquisition> segment_acquisition;
};

// Bounded native CPU reference for R10-D line construction.  It consumes
// canonical SpectrumFrame objects after DSP, never raw I/Q, and it has no
// Python or Qt dependency.  The coordinator owning RF retunes will submit
// one segment frame per declared line sequence.
class ContinuousSweepLineAssembler final {
public:
    explicit ContinuousSweepLineAssembler(SweepLineDefinition definition);

    [[nodiscard]] std::vector<SweepLineFrame> admit(
        std::uint64_t line_sequence,
        std::int64_t completed_ns,
        SweepLineSegmentFrame segment
    );

    // Publish a terminal line without admitting a segment.  Coordinators use
    // this for cancellation/disconnect/reconfigure boundaries so no partial
    // RF epoch can be silently presented as a completed sweep line.
    [[nodiscard]] SweepLineFrame emit_gap(
        std::uint64_t line_sequence,
        std::int64_t completed_ns,
        SweepLineGapReason reason
    );

    // Complete all staged lines as explicit control gaps in sequence order.
    [[nodiscard]] std::vector<SweepLineFrame> flush(SweepLineGapReason reason);

    [[nodiscard]] const SweepLineDefinition& definition() const noexcept;
    [[nodiscard]] SweepLineAssemblyMetrics metrics() const noexcept;

    // On-demand coherent snapshot; owner must bound publication cadence.
    // This neither consumes the pending line nor increments terminal counters.
    [[nodiscard]] std::optional<SweepProgressFrame> preview(std::uint64_t line_sequence) const;

    // Explicit one-time readback binding for a plan's unresolved generation.
    // Zero is never treated as an admission wildcard.
    void bind_segment_generation(std::uint32_t segment_index, std::uint64_t applied_generation);

private:
    struct PendingLine {
        std::int64_t completed_ns{};
        std::map<std::uint32_t, SweepLineSegmentFrame> segments;
        std::vector<double> power_sum;
        std::vector<std::uint32_t> coverage;
        std::vector<std::uint32_t> quality;
        std::vector<std::int32_t> source_indices;
    };

    [[nodiscard]] SweepLineFrame finalise(
        std::uint64_t line_sequence,
        const PendingLine& pending,
        SweepLineGapReason forced_gap_reason
    ) const;

    SweepLineDefinition definition_;
    SharedArray<double> frequencies_;
    std::map<std::uint64_t, PendingLine> pending_;
    std::uint64_t completed_lines_{};
    std::uint64_t gapped_lines_{};
    std::uint64_t capacity_evicted_lines_{};
};

}  // namespace sdr_core
