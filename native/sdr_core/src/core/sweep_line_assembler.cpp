#include "sdr_core/sweep_line_assembler.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <memory>
#include <string>
#include <utility>

namespace sdr_core {
namespace {

constexpr std::uint32_t max_target_bins = 2'000'000U;

[[nodiscard]] std::uint32_t flags(const QualityFlag value) noexcept {
    return static_cast<std::uint32_t>(value);
}

[[nodiscard]] bool same_source(const SourceDescriptor& left, const SourceDescriptor& right) noexcept {
    return left.source_id == right.source_id && left.source_type == right.source_type;
}

[[nodiscard]] float db_from_power(const double power) noexcept {
    return power > 0.0
        ? static_cast<float>(10.0 * std::log10(power))
        : -std::numeric_limits<float>::infinity();
}

void accumulate_segment_power(
    const std::vector<double>& frequencies,
    const std::vector<float>& values,
    const std::size_t target_begin,
    const std::size_t target_end,
    const std::vector<double>& target_frequencies,
    std::vector<double>& power_sum,
    std::vector<std::uint32_t>& coverage,
    std::vector<std::uint32_t>& quality,
    const std::uint32_t input_quality,
    std::vector<std::int32_t>& source_indices,
    const std::uint32_t source_segment_index
) {
    // `validate(SpectrumFrame)` establishes this monotonic grid.  Convert
    // each source dB value once, then advance the source cursor with the
    // final analysis grid instead of lower-bounding every target bin.  The
    // temporary is per-segment and bounded by the physical FFT maximum.
    std::vector<double> source_power(values.size(), std::numeric_limits<double>::quiet_NaN());
    for (std::size_t index = 0U; index < values.size(); ++index) {
        if (std::isfinite(values[index])) {
            source_power[index] = std::pow(10.0, static_cast<double>(values[index]) / 10.0);
        }
    }
    if (target_begin >= target_end) {
        return;
    }
    auto upper = std::lower_bound(
        frequencies.begin(), frequencies.end(), target_frequencies[target_begin]
    );
    for (auto target = target_begin; target < target_end; ++target) {
        const auto frequency = target_frequencies[target];
        while (upper != frequencies.end() && *upper < frequency) {
            ++upper;
        }
        double power = std::numeric_limits<double>::quiet_NaN();
        if (upper != frequencies.end() && *upper == frequency) {
            power = source_power[static_cast<std::size_t>(upper - frequencies.begin())];
        } else if (upper != frequencies.begin() && upper != frequencies.end()) {
            const auto right = static_cast<std::size_t>(upper - frequencies.begin());
            const auto left = right - 1U;
            const auto left_power = source_power[left];
            const auto right_power = source_power[right];
            if (std::isfinite(left_power) && std::isfinite(right_power)) {
                const auto fraction = (frequency - frequencies[left]) /
                    (frequencies[right] - frequencies[left]);
                power = left_power + (right_power - left_power) * fraction;
            }
        }
        if (!std::isfinite(power) || power < 0.0) {
            continue;
        }
        power_sum[target] += power;
        ++coverage[target];
        // Preserve every contributing frame's quality, including unknown
        // future wire bits. An unusable value contributes neither power nor flags.
        quality[target] |= input_quality;
        // Definition indices are strictly increasing. Retain its first
        // contributor regardless of segment arrival order.
        if (source_indices[target] < 0 ||
            source_segment_index < static_cast<std::uint32_t>(source_indices[target])) {
            source_indices[target] = static_cast<std::int32_t>(source_segment_index);
        }
    }
}

}  // namespace

ContinuousSweepLineAssembler::ContinuousSweepLineAssembler(SweepLineDefinition definition)
    : definition_(std::move(definition)) {
    validate(definition_);
    const auto span = definition_.stop_frequency_hz - definition_.start_frequency_hz;
    const auto count_double = definition_.analysis_bins_per_usable_window == 0U
        ? std::floor(span / definition_.target_spacing_hz) + 1.0
        : std::ceil(span / definition_.target_spacing_hz - 1e-12);
    if (!std::isfinite(count_double) || count_double < 2.0 || count_double > max_target_bins) {
        throw ConfigurationError("sweep-line target grid exceeds its bounded bin limit");
    }
    auto grid = std::make_shared<std::vector<double>>(static_cast<std::size_t>(count_double));
    for (std::size_t index = 0; index < grid->size(); ++index) {
        (*grid)[index] = definition_.start_frequency_hz + index * definition_.target_spacing_hz;
    }
    frequencies_ = std::move(grid);
}

std::vector<SweepLineFrame> ContinuousSweepLineAssembler::admit(
    const std::uint64_t line_sequence,
    const std::int64_t completed_ns,
    SweepLineSegmentFrame segment
) {
    if (completed_ns < 0) {
        throw ConfigurationError("sweep-line completion timestamp must be non-negative");
    }
    validate(segment);
    const auto expected = std::find_if(
        definition_.segments.begin(), definition_.segments.end(),
        [&segment](const SweepLineSegmentDefinition& item) {
            return item.segment_index == segment.segment_index;
        }
    );
    if (expected == definition_.segments.end()) {
        throw ConfigurationError("sweep-line segment is outside its definition");
    }
    if (!same_source(segment.spectrum.source, definition_.source) ||
        segment.spectrum.unit != definition_.unit ||
        segment.spectrum.config_generation != expected->config_generation) {
        throw ConfigurationError("sweep-line segment provenance differs from its definition");
    }

    std::vector<SweepLineFrame> emitted;
    emitted.reserve(2U);
    auto pending = pending_.find(line_sequence);
    if (pending == pending_.end()) {
        // Allocate all staging arrays before publishing a pending entry.
        // A failed allocation must never leave mismatched vector sizes.
        PendingLine initial{.completed_ns = completed_ns};
        initial.power_sum.assign(frequencies_->size(), 0.0);
        initial.coverage.assign(frequencies_->size(), 0U);
        initial.quality.assign(frequencies_->size(), 0U);
        initial.source_indices.assign(frequencies_->size(), -1);
        if (pending_.size() >= definition_.max_inflight_lines) {
            const auto evicted = pending_.begin();
            emitted.push_back(finalise(
                evicted->first, evicted->second, SweepLineGapReason::Capacity
            ));
            ++gapped_lines_;
            ++capacity_evicted_lines_;
            pending_.erase(evicted);
        }
        pending = pending_.emplace(line_sequence, std::move(initial)).first;
    } else {
        if (pending->second.segments.contains(segment.segment_index)) {
            throw ConfigurationError("sweep-line segment was admitted more than once");
        }
    }
    auto& staging = pending->second;
    const auto target_begin = static_cast<std::size_t>(std::lower_bound(
        frequencies_->begin(), frequencies_->end(), expected->usable_start_hz
    ) - frequencies_->begin());
    const auto target_end = static_cast<std::size_t>(std::upper_bound(
        frequencies_->begin(), frequencies_->end(), expected->usable_stop_hz
    ) - frequencies_->begin());
    const auto segment_index = segment.segment_index;
    const auto [stored, inserted] = staging.segments.emplace(segment_index, std::move(segment));
    if (!inserted) {
        throw ConfigurationError("sweep-line segment was admitted more than once");
    }
    const auto& spectrum = stored->second.spectrum;
    try {
        // The helper allocates source_power before modifying any accumulator.
        accumulate_segment_power(
        *spectrum.frequencies_hz, *spectrum.values,
        target_begin, target_end, *frequencies_, staging.power_sum,
        staging.coverage, staging.quality, flags(spectrum.quality_flags),
        staging.source_indices, segment_index
        );
    } catch (...) {
        staging.segments.erase(stored);
        throw;
    }
    staging.completed_ns = std::max(staging.completed_ns, completed_ns);
    // Commit only after all admission checks and accumulation succeeded.
    // Definition/map order and timestamps need not match arrival order.
    staging.last_admitted_segment = *expected;
    if (pending->second.segments.size() == definition_.segments.size()) {
        auto line = finalise(line_sequence, pending->second, SweepLineGapReason::MissingSegment);
        if (line.state == SweepLineState::Complete) {
            ++completed_lines_;
        } else {
            ++gapped_lines_;
        }
        emitted.push_back(std::move(line));
        pending_.erase(pending);
    }
    return emitted;
}

void ContinuousSweepLineAssembler::bind_segment_generation(
    const std::uint32_t segment_index,
    const std::uint64_t applied_generation
) {
    const auto expected = std::find_if(definition_.segments.begin(), definition_.segments.end(),
        [segment_index](const auto& item) { return item.segment_index == segment_index; });
    if (applied_generation == 0 || expected == definition_.segments.end() ||
        expected->config_generation != 0) {
        throw ConfigurationError("segment generation requires an unresolved plan and nonzero readback");
    }
    for (const auto& [sequence, pending] : pending_) {
        if (pending.segments.contains(segment_index)) {
            throw ConfigurationError("cannot rebind an admitted sweep segment");
        }
    }
    expected->config_generation = applied_generation;
}

std::vector<SweepLineFrame> ContinuousSweepLineAssembler::flush(const SweepLineGapReason reason) {
    std::vector<SweepLineFrame> emitted;
    emitted.reserve(pending_.size());
    for (const auto& [sequence, pending] : pending_) {
        emitted.push_back(finalise(sequence, pending, reason));
        ++gapped_lines_;
    }
    pending_.clear();
    return emitted;
}

SweepLineFrame ContinuousSweepLineAssembler::emit_gap(
    const std::uint64_t line_sequence,
    const std::int64_t completed_ns,
    const SweepLineGapReason reason
) {
    if (completed_ns < 0) {
        throw ConfigurationError("sweep-line completion timestamp must be non-negative");
    }
    if (pending_.contains(line_sequence)) {
        throw ConfigurationError("cannot emit a terminal gap for a pending sweep line");
    }
    PendingLine pending{.completed_ns = completed_ns};
    auto result = finalise(line_sequence, pending, reason);
    ++gapped_lines_;
    return result;
}

const SweepLineDefinition& ContinuousSweepLineAssembler::definition() const noexcept {
    return definition_;
}

SweepLineAssemblyMetrics ContinuousSweepLineAssembler::metrics() const noexcept {
    return {
        .completed_lines = completed_lines_,
        .gapped_lines = gapped_lines_,
        .capacity_evicted_lines = capacity_evicted_lines_,
        .pending_lines = static_cast<std::uint32_t>(pending_.size()),
    };
}

SweepLineFrame ContinuousSweepLineAssembler::finalise(
    const std::uint64_t line_sequence,
    const PendingLine& pending,
    const SweepLineGapReason forced_gap_reason
) const {
    const auto count = frequencies_->size();
    auto values = std::make_shared<std::vector<float>>(
        count, std::numeric_limits<float>::quiet_NaN()
    );
    auto quality = std::make_shared<std::vector<std::uint32_t>>(count, 0U);
    auto source_indices = std::make_shared<std::vector<std::int32_t>>(count, -1);
    if (!pending.power_sum.empty()) {
        *quality = pending.quality;
        *source_indices = pending.source_indices;
    }

    std::vector<std::uint32_t> missing;
    std::vector<SweepSegmentAcquisition> acquired;
    acquired.reserve(pending.segments.size());
    for (const auto& definition_segment : definition_.segments) {
        const auto found = pending.segments.find(definition_segment.segment_index);
        if (found == pending.segments.end()) {
            missing.push_back(definition_segment.segment_index);
            continue;
        }
        const auto& frame = found->second.spectrum;
        acquired.push_back({
            .segment_index = definition_segment.segment_index,
            .config_generation = frame.config_generation,
            .frame_sequence = frame.frame_sequence,
            .first_sample_index = frame.first_sample_index,
            .timestamp_ns = frame.timestamp_ns,
            .sample_rate_hz = frame.sample_rate_hz,
            .fft_size = frame.fft_size,
            .quality_flags = frame.quality_flags,
        });
    }

    bool missing_bins = false;
    for (std::size_t index = 0U; index < count; ++index) {
        if (pending.coverage.empty() || pending.coverage[index] == 0U) {
            (*quality)[index] = flags(QualityFlag::MissingSegment);
            missing_bins = true;
            continue;
        }
        (*values)[index] = db_from_power(pending.power_sum[index] / static_cast<double>(pending.coverage[index]));
        if (pending.coverage[index] > 1U) {
            (*quality)[index] |= flags(QualityFlag::StitchOverlap);
        }
    }
    const bool complete = missing.empty() && !missing_bins;
    std::vector<SweepLineGapReason> reasons;
    if (!complete) {
        reasons.push_back(forced_gap_reason);
    }
    SweepLineFrame result{
        .source = definition_.source,
        .line_sequence = line_sequence,
        .epoch = definition_.epoch,
        .completed_ns = pending.completed_ns,
        .state = complete ? SweepLineState::Complete : SweepLineState::Gap,
        .start_frequency_hz = definition_.start_frequency_hz,
        .stop_frequency_hz = definition_.stop_frequency_hz,
        .target_spacing_hz = definition_.target_spacing_hz,
        .analysis_window_hz = definition_.analysis_window_hz,
        .analysis_bins_per_usable_window = definition_.analysis_bins_per_usable_window,
        .physical_fft_bin_width_hz = definition_.physical_fft_bin_width_hz,
        .physical_fft_size = definition_.physical_fft_size,
        .unit = definition_.unit,
        .frequencies_hz = frequencies_,
        .values = values,
        .quality_flags_per_bin = quality,
        .source_segment_indices = source_indices,
        .missing_segment_indices = std::move(missing),
        .segment_generations = definition_.segments,
        .gap_reasons = std::move(reasons),
        .acquired_segments = std::move(acquired),
        .last_admitted_segment = pending.last_admitted_segment,
    };
    validate(result);
    return result;
}

std::optional<SweepProgressFrame> ContinuousSweepLineAssembler::preview(
    const std::uint64_t line_sequence
) const {
    const auto found = pending_.find(line_sequence);
    if (found == pending_.end() || found->second.segments.empty()) {
        return std::nullopt;
    }
    const auto& pending = found->second;
    // Reuse cached-power materialisation, not segment interpolation. The
    // temporary terminal-shaped result never escapes this adapter.
    auto view = finalise(line_sequence, pending, SweepLineGapReason::MissingSegment);
    std::vector<SweepLineSegmentDefinition> acquired;
    acquired.reserve(pending.segments.size());
    for (const auto& definition : definition_.segments) {
        if (pending.segments.contains(definition.segment_index)) {
            acquired.push_back(definition);
        }
    }
    return SweepProgressFrame{
        .source = view.source,
        .line_sequence = line_sequence,
        .epoch = view.epoch,
        .revision = pending.segments.size(),
        .unit = view.unit,
        .frequencies_hz = view.frequencies_hz,
        .values = view.values,
        .quality_flags_per_bin = view.quality_flags_per_bin,
        .source_segment_indices = view.source_segment_indices,
        .acquired_segments = std::move(acquired),
        .pending_segment_indices = std::move(view.missing_segment_indices),
        .segment_acquisition = std::move(view.acquired_segments),
        .last_admitted_segment = view.last_admitted_segment,
    };
}

}  // namespace sdr_core
