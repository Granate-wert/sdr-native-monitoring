#include "sdr_hackrf/hackrf_sweep_sequence.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <utility>

namespace sdr_hackrf {
namespace {

constexpr std::uint64_t one_mhz = 1'000'000U;

std::vector<HackrfSweepExpectedBlock> make_expected(
    const HackrfSweepSequencePlan& plan
) {
    if (plan.ranges.empty() || plan.ranges.size() > hackrf_sweep_max_ranges ||
        plan.step_width_hz == 0U ||
        plan.max_transfer_blocks == 0U ||
        plan.max_transfer_blocks > hackrf_sweep_max_transfer_blocks ||
        (plan.style != HackrfSweepStyle::Linear &&
         plan.style != HackrfSweepStyle::Interleaved) ||
        (plan.style == HackrfSweepStyle::Interleaved &&
         (plan.step_width_hz % 4U) != 0U)) {
        throw sdr_core::ConfigurationError("HackRF Sweep header plan is invalid");
    }
    std::size_t total_blocks = 0U;
    std::uint16_t previous_stop = 0U;
    for (std::size_t index = 0U; index < plan.ranges.size(); ++index) {
        const auto range = plan.ranges[index];
        if (range.start_mhz >= range.stop_mhz ||
            (index != 0U && range.start_mhz < previous_stop)) {
            throw sdr_core::ConfigurationError("HackRF Sweep range/step plan is invalid");
        }
        const std::uint64_t width_hz =
            (static_cast<std::uint64_t>(range.stop_mhz) - range.start_mhz) * one_mhz;
        if ((width_hz % plan.step_width_hz) != 0U) {
            throw sdr_core::ConfigurationError("HackRF Sweep range/step plan is invalid");
        }
        const auto steps = width_hz / plan.step_width_hz;
        const auto phases = plan.style == HackrfSweepStyle::Interleaved ? 2U : 1U;
        if (steps == 0U ||
            steps > (hackrf_sweep_max_expected_blocks - total_blocks) / phases) {
            throw sdr_core::ConfigurationError("HackRF Sweep header plan exceeds bound");
        }
        total_blocks += static_cast<std::size_t>(steps) * phases;
        previous_stop = range.stop_mhz;
    }
    std::vector<HackrfSweepExpectedBlock> result;
    result.reserve(total_blocks);
    for (std::size_t range_index = 0U; range_index < plan.ranges.size(); ++range_index) {
        const auto range = plan.ranges[range_index];
        const auto steps =
            (static_cast<std::uint64_t>(range.stop_mhz - range.start_mhz) * one_mhz) /
            plan.step_width_hz;
        for (std::uint64_t step = 0U; step < steps; ++step) {
            const auto base = static_cast<std::uint64_t>(range.start_mhz) * one_mhz +
                              step * plan.step_width_hz;
            result.push_back({base, static_cast<std::uint32_t>(range_index),
                              static_cast<std::uint32_t>(step), 0U});
            if (plan.style == HackrfSweepStyle::Interleaved) {
                result.push_back({base + plan.step_width_hz / 4U,
                                  static_cast<std::uint32_t>(range_index),
                                  static_cast<std::uint32_t>(step), 1U});
            }
        }
    }
    return result;
}

}  // namespace

HackrfSweepSequenceGate::HackrfSweepSequenceGate(HackrfSweepSequencePlan plan)
    : plan_(std::move(plan)), expected_(make_expected(plan_)) {}

const HackrfSweepSequencePlan& HackrfSweepSequenceGate::plan() const noexcept {
    return plan_;
}

std::span<const HackrfSweepExpectedBlock>
HackrfSweepSequenceGate::expected_blocks() const noexcept {
    return expected_;
}

HackrfSweepSequenceMetrics HackrfSweepSequenceGate::metrics() const noexcept {
    auto result = metrics_;
    result.gap_pending = pending_gap_;
    return result;
}

void HackrfSweepSequenceGate::note_unlocated_rejection() noexcept {
    ++metrics_.unlocated_rejections;
    pending_gap_ = true;
}

void HackrfSweepSequenceGate::note_downstream_drop() noexcept {
    ++metrics_.downstream_drops;
    pending_gap_ = true;
}

HackrfSweepBlockDecision HackrfSweepSequenceGate::admit_one(
    const std::span<const std::uint8_t> bytes
) noexcept {
    ++metrics_.blocks_seen;
    const auto parsed = parse_hackrf_sweep_block(bytes);
    if (!parsed.syntax_valid()) {
        ++metrics_.invalid_markers;
        note_unlocated_rejection();
        return {};
    }
    const auto frequency_hz = parsed.block.reported_tuned_frequency_hz;
    const auto found = std::lower_bound(
        expected_.begin(), expected_.end(), frequency_hz,
        [](const HackrfSweepExpectedBlock& entry, const std::uint64_t frequency) {
            return entry.reported_tuned_frequency_hz < frequency;
        }
    );
    if (found == expected_.end() || found->reported_tuned_frequency_hz != frequency_hz) {
        ++metrics_.out_of_plan;
        note_unlocated_rejection();
        return {.status = HackrfSweepBlockDecisionStatus::OutOfPlan,
                .reported_tuned_frequency_hz = frequency_hz};
    }
    const auto index = static_cast<std::uint32_t>(found - expected_.begin());
    if (!metrics_.synchronized && index != 0U) {
        ++metrics_.awaiting_first_frequency;
        note_unlocated_rejection();
        return {.status = HackrfSweepBlockDecisionStatus::AwaitingFirstFrequency,
                .reported_tuned_frequency_hz = frequency_hz};
    }
    if (metrics_.synchronized && index != 0U &&
        index < metrics_.next_plan_index) {
        ++metrics_.out_of_order;
        note_unlocated_rejection();
        return {.status = HackrfSweepBlockDecisionStatus::OutOfOrder,
                .reported_tuned_frequency_hz = frequency_hz};
    }
    if (metrics_.synchronized && index == 0U &&
        metrics_.next_plan_index == 1U && expected_.size() > 1U) {
        // An immediate repeated origin is ambiguous: it could be a duplicate
        // or an entire missed lap. Keep waiting for the next distinct planned
        // header instead of publishing any repeated origin as a new scan.
        ++metrics_.out_of_order;
        note_unlocated_rejection();
        return {.status = HackrfSweepBlockDecisionStatus::OutOfOrder,
                .reported_tuned_frequency_hz = frequency_hz};
    }
    std::uint32_t skipped = 0U;
    bool new_scan = false;
    if (index == 0U) {
        new_scan = true;
        if (metrics_.synchronized && metrics_.next_plan_index < expected_.size()) {
            skipped = static_cast<std::uint32_t>(
                expected_.size() - metrics_.next_plan_index
            );
        }
        ++metrics_.scan_epoch;
        metrics_.synchronized = true;
    } else {
        skipped = index - metrics_.next_plan_index;
    }
    const bool gap = pending_gap_ || skipped != 0U;
    if (metrics_.continuity_epoch == 0U || gap) {
        ++metrics_.continuity_epoch;
    }
    metrics_.known_skipped_headers += skipped;
    metrics_.next_plan_index = index + 1U;
    ++metrics_.blocks_admitted;
    pending_gap_ = false;
    return {
        .status = HackrfSweepBlockDecisionStatus::Admitted,
        .interleaved_ci8 = parsed.block.interleaved_ci8,
        .reported_tuned_frequency_hz = frequency_hz,
        .scan_epoch = metrics_.scan_epoch,
        .continuity_epoch = metrics_.continuity_epoch,
        .plan_index = index,
        .range_index = found->range_index,
        .step_index = found->step_index,
        .interleave_phase = found->interleave_phase,
        .known_skipped_headers_before = skipped,
        .gap_before = gap,
        .new_scan = new_scan,
    };
}

HackrfSweepTransferResult HackrfSweepSequenceGate::admit_transfer(
    const std::span<const std::uint8_t> callback_bytes,
    const HackrfSweepBlockSink sink,
    void* const context
) noexcept {
    ++metrics_.transfers_seen;
    if (callback_bytes.empty() ||
        (callback_bytes.size() % hackrf_sweep_block_bytes) != 0U) {
        ++metrics_.transfers_rejected;
        note_unlocated_rejection();
        return {HackrfSweepTransferStatus::MalformedLength, 0U};
    }
    const auto count = callback_bytes.size() / hackrf_sweep_block_bytes;
    if (count > plan_.max_transfer_blocks) {
        ++metrics_.transfers_rejected;
        note_unlocated_rejection();
        return {HackrfSweepTransferStatus::AboveConfiguredLimit, 0U};
    }
    if (sink == nullptr) {
        ++metrics_.transfers_rejected;
        note_unlocated_rejection();
        return {HackrfSweepTransferStatus::SinkMissing, 0U};
    }
    for (std::size_t index = 0U; index < count; ++index) {
        const auto decision = admit_one(callback_bytes.subspan(
            index * hackrf_sweep_block_bytes, hackrf_sweep_block_bytes
        ));
        if (!sink(decision, context) && decision.admitted()) {
            note_downstream_drop();
        }
    }
    return {HackrfSweepTransferStatus::Processed, count};
}

}  // namespace sdr_hackrf
