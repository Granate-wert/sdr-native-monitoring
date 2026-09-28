#include "sdr_hackrf/hackrf_sweep_sequence.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <limits>
#include <span>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void fill_block(const std::span<std::uint8_t> bytes, const std::uint64_t frequency_hz) {
    expect(bytes.size() == sdr_hackrf::hackrf_sweep_block_bytes, "fixture size");
    std::fill(bytes.begin(), bytes.end(), static_cast<std::uint8_t>(0x80U));
    bytes[0] = 0x7fU;
    bytes[1] = 0x7fU;
    for (std::size_t index = 0U; index < 8U; ++index) {
        bytes[index + 2U] = static_cast<std::uint8_t>(frequency_hz >> (index * 8U));
    }
}

std::vector<std::uint8_t> transfer(const std::span<const std::uint64_t> frequencies) {
    std::vector<std::uint8_t> bytes(
        frequencies.size() * sdr_hackrf::hackrf_sweep_block_bytes
    );
    for (std::size_t index = 0U; index < frequencies.size(); ++index) {
        fill_block(std::span<std::uint8_t>(bytes).subspan(
            index * sdr_hackrf::hackrf_sweep_block_bytes,
            sdr_hackrf::hackrf_sweep_block_bytes
        ), frequencies[index]);
    }
    return bytes;
}

struct Collector {
    std::span<sdr_hackrf::HackrfSweepBlockDecision> output;
    std::size_t used{};
    std::size_t reject_at{std::numeric_limits<std::size_t>::max()};
};

bool collect(const sdr_hackrf::HackrfSweepBlockDecision& decision,
             void* const context) noexcept {
    auto& target = *static_cast<Collector*>(context);
    if (target.used >= target.output.size()) {
        return false;
    }
    target.output[target.used] = decision;
    return target.used++ != target.reject_at;
}

sdr_hackrf::HackrfSweepTransferResult admit(
    sdr_hackrf::HackrfSweepSequenceGate& gate,
    const std::span<const std::uint8_t> bytes,
    const std::span<sdr_hackrf::HackrfSweepBlockDecision> output,
    const std::size_t reject_at = std::numeric_limits<std::size_t>::max()
) {
    Collector target{output, 0U, reject_at};
    const auto result = gate.admit_transfer(bytes, &collect, &target);
    expect(target.used == result.blocks_visited, "sink must visit each processed block");
    return result;
}

sdr_hackrf::HackrfSweepSequencePlan example_plan() {
    return {.ranges = {{100U, 140U}, {200U, 220U}}};
}

void pinned_interleaved_order_and_epochs() {
    sdr_hackrf::HackrfSweepSequenceGate gate(example_plan());
    const auto expected = gate.expected_blocks();
    const std::array<std::uint64_t, 6> frequencies{
        100'000'000U, 105'000'000U, 120'000'000U,
        125'000'000U, 200'000'000U, 205'000'000U,
    };
    expect(expected.size() == frequencies.size(), "expected two blocks per tune step");
    for (std::size_t index = 0U; index < expected.size(); ++index) {
        expect(expected[index].reported_tuned_frequency_hz == frequencies[index],
               "pinned interleaved header order");
    }
    expect(expected[2].range_index == 0U && expected[2].step_index == 1U &&
           expected[2].interleave_phase == 0U &&
           expected[5].range_index == 1U && expected[5].interleave_phase == 1U,
           "range, step and phase identity must survive the plan");
    auto bytes = transfer(frequencies);
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 6> decisions{};
    const auto result = admit(gate, bytes, decisions);
    expect(result.status == sdr_hackrf::HackrfSweepTransferStatus::Processed &&
           result.blocks_visited == frequencies.size(), "one callback yields six decisions");
    for (std::size_t index = 0U; index < decisions.size(); ++index) {
        const auto& decision = decisions[index];
        expect(decision.admitted() && decision.plan_index == index &&
               decision.reported_tuned_frequency_hz == frequencies[index] &&
               decision.scan_epoch == 1U && decision.continuity_epoch == 1U &&
               !decision.gap_before && decision.new_scan == (index == 0U),
               "nominal first pass metadata");
        expect(decision.interleaved_ci8.data() ==
               bytes.data() + index * sdr_hackrf::hackrf_sweep_block_bytes +
                   sdr_hackrf::hackrf_sweep_header_bytes,
               "accepted CI8 view must borrow the correct callback block");
    }
    const std::array<std::uint64_t, 1> restart{frequencies.front()};
    auto next = transfer(restart);
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 1> second{};
    expect(admit(gate, next, second).blocks_visited == 1U &&
           second[0].admitted() && second[0].new_scan &&
           second[0].scan_epoch == 2U && second[0].continuity_epoch == 1U &&
           !second[0].gap_before, "complete wrap starts a new scan without fake loss");
    const auto metrics = gate.metrics();
    expect(metrics.blocks_admitted == 7U && metrics.known_skipped_headers == 0U &&
           metrics.synchronized && metrics.next_plan_index == 1U,
           "nominal cumulative metrics");
}

void missing_corrupt_and_out_of_order_are_visible() {
    sdr_hackrf::HackrfSweepSequenceGate gate(example_plan());
    const std::array<std::uint64_t, 3> frequencies{
        100'000'000U, 105'000'000U, 120'000'000U,
    };
    auto bytes = transfer(frequencies);
    bytes[sdr_hackrf::hackrf_sweep_block_bytes] = 0U;
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 3> decisions{};
    expect(admit(gate, bytes, decisions).blocks_visited == 3U,
           "corrupt middle block must not shift transfer alignment");
    expect(decisions[0].admitted() &&
           decisions[1].status == sdr_hackrf::HackrfSweepBlockDecisionStatus::InvalidMarker &&
           decisions[1].interleaved_ci8.empty() &&
           decisions[2].admitted() && decisions[2].gap_before &&
           decisions[2].known_skipped_headers_before == 1U &&
           decisions[2].continuity_epoch == 2U,
           "corrupt block and known index jump must not become contiguous RF");
    const std::array<std::uint64_t, 1> duplicate{120'000'000U};
    auto repeated = transfer(duplicate);
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 1> later{};
    static_cast<void>(admit(gate, repeated, later));
    expect(later[0].status == sdr_hackrf::HackrfSweepBlockDecisionStatus::OutOfOrder,
           "duplicate header is not another segment");
    const std::array<std::uint64_t, 1> next{125'000'000U};
    auto next_bytes = transfer(next);
    static_cast<void>(admit(gate, next_bytes, later));
    expect(later[0].admitted() && later[0].gap_before &&
           later[0].continuity_epoch == 3U &&
           later[0].known_skipped_headers_before == 0U,
           "duplicate must mark discontinuity without inventing a missing header");
    const auto metrics = gate.metrics();
    expect(metrics.invalid_markers == 1U && metrics.out_of_order == 1U &&
           metrics.known_skipped_headers == 1U &&
           metrics.unlocated_rejections == 2U,
           "loss counters must stay in their distinct categories");
}

void first_frequency_sync_and_early_restart() {
    sdr_hackrf::HackrfSweepSequenceGate gate(example_plan());
    const std::array<std::uint64_t, 1> mid{105'000'000U};
    auto mid_bytes = transfer(mid);
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 1> decision{};
    static_cast<void>(admit(gate, mid_bytes, decision));
    expect(decision[0].status ==
           sdr_hackrf::HackrfSweepBlockDecisionStatus::AwaitingFirstFrequency,
           "a mid-scan first callback must not fabricate scan origin");
    const std::array<std::uint64_t, 2> start{100'000'000U, 105'000'000U};
    auto start_bytes = transfer(start);
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 2> admitted{};
    static_cast<void>(admit(gate, start_bytes, admitted));
    expect(admitted[0].admitted() && admitted[0].new_scan && admitted[0].gap_before &&
           admitted[0].scan_epoch == 1U && admitted[1].admitted(),
           "first complete plan origin starts an explicitly gapped scan");
    const std::array<std::uint64_t, 1> restart{100'000'000U};
    auto restart_bytes = transfer(restart);
    static_cast<void>(admit(gate, restart_bytes, decision));
    expect(decision[0].admitted() && decision[0].new_scan &&
           decision[0].scan_epoch == 2U && decision[0].continuity_epoch == 2U &&
           decision[0].gap_before && decision[0].known_skipped_headers_before == 4U,
           "early origin makes missing tail and new scan explicit");
}

void transfer_and_downstream_failures_do_not_partially_admit() {
    sdr_hackrf::HackrfSweepSequenceGate gate(example_plan());
    const std::array<std::uint64_t, 1> start{100'000'000U};
    auto good = transfer(start);
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 1> decision{};
    static_cast<void>(admit(gate, good, decision));
    expect(decision[0].admitted(), "precondition first header");
    const auto malformed = admit(
        gate, std::span<const std::uint8_t>(good).first(good.size() - 1U), decision);
    expect(malformed.status == sdr_hackrf::HackrfSweepTransferStatus::MalformedLength &&
           malformed.blocks_visited == 0U, "partial callback must be rejected whole");
    const std::array<std::uint64_t, 2> pair{105'000'000U, 120'000'000U};
    auto pair_bytes = transfer(pair);
    expect(gate.admit_transfer(pair_bytes, nullptr, nullptr).status ==
           sdr_hackrf::HackrfSweepTransferStatus::SinkMissing,
           "missing sink must reject before any block advances state");
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 2> enough{};
    static_cast<void>(admit(gate, pair_bytes, enough));
    expect(enough[0].admitted() && enough[0].gap_before &&
           enough[0].plan_index == 1U && enough[1].admitted() &&
           !enough[1].gap_before, "recovery must preserve exact header order");
    gate.note_downstream_drop();
    const std::array<std::uint64_t, 1> next{125'000'000U};
    auto next_bytes = transfer(next);
    static_cast<void>(admit(gate, next_bytes, decision));
    expect(decision[0].admitted() && decision[0].gap_before &&
           decision[0].continuity_epoch == 3U,
           "post-gate delivery loss must not be silently stitched");
    const auto metrics = gate.metrics();
    expect(metrics.transfers_rejected == 2U && metrics.downstream_drops == 1U &&
           metrics.blocks_admitted == 4U && metrics.unlocated_rejections == 2U,
           "host-side rejection categories must remain distinct");
}

void same_transfer_sink_drop_marks_the_next_block() {
    sdr_hackrf::HackrfSweepSequenceGate gate(example_plan());
    const std::array<std::uint64_t, 3> values{
        100'000'000U, 105'000'000U, 120'000'000U,
    };
    auto bytes = transfer(values);
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 3> decisions{};
    expect(admit(gate, bytes, decisions, 1U).blocks_visited == 3U,
           "every callback block must visit the synchronous sink");
    expect(decisions[0].admitted() && decisions[1].admitted() &&
           !decisions[1].gap_before && decisions[2].admitted() &&
           decisions[2].gap_before && decisions[2].continuity_epoch == 2U &&
           decisions[2].known_skipped_headers_before == 0U &&
           gate.metrics().downstream_drops == 1U,
           "a sink drop must affect the very next header in the SAME transfer");
}

void repeated_origin_stays_in_existing_scan() {
    sdr_hackrf::HackrfSweepSequenceGate gate(example_plan());
    const std::array<std::uint64_t, 3> values{
        100'000'000U, 100'000'000U, 100'000'000U,
    };
    auto bytes = transfer(values);
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 3> decisions{};
    static_cast<void>(admit(gate, bytes, decisions));
    expect(decisions[0].admitted() &&
           decisions[1].status == sdr_hackrf::HackrfSweepBlockDecisionStatus::OutOfOrder &&
           decisions[2].status == sdr_hackrf::HackrfSweepBlockDecisionStatus::OutOfOrder &&
           gate.metrics().synchronized && gate.metrics().scan_epoch == 1U &&
           gate.metrics().next_plan_index == 1U,
           "repeated origins cannot prove a new firmware cycle");
    const std::array<std::uint64_t, 1> mid{105'000'000U};
    auto mid_bytes = transfer(mid);
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 1> next{};
    static_cast<void>(admit(gate, mid_bytes, next));
    expect(next[0].admitted() && next[0].gap_before &&
           next[0].scan_epoch == 1U && next[0].continuity_epoch == 2U &&
           !next[0].new_scan,
           "distinct expected progress resumes only the existing gapped scan");
    const std::array<std::uint64_t, 1> restart{100'000'000U};
    auto restart_bytes = transfer(restart);
    static_cast<void>(admit(gate, restart_bytes, next));
    expect(next[0].admitted() && next[0].new_scan && next[0].gap_before &&
           next[0].scan_epoch == 2U && next[0].continuity_epoch == 3U &&
           next[0].known_skipped_headers_before == 4U,
           "early restart after progress starts an explicit partial scan");
}

void out_of_plan_and_linear_mode() {
    sdr_hackrf::HackrfSweepSequenceGate gate(example_plan());
    const std::array<std::uint64_t, 3> values{
        100'000'000U, 106'000'000U, 120'000'000U,
    };
    auto bytes = transfer(values);
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 3> result{};
    static_cast<void>(admit(gate, bytes, result));
    expect(result[1].status == sdr_hackrf::HackrfSweepBlockDecisionStatus::OutOfPlan &&
           result[2].admitted() && result[2].gap_before &&
           result[2].known_skipped_headers_before == 1U,
           "out-of-plan header must not be painted at its claimed frequency");
    sdr_hackrf::HackrfSweepSequencePlan linear{
        .ranges = {{7'000U, 7'040U}},
        .style = sdr_hackrf::HackrfSweepStyle::Linear,
    };
    sdr_hackrf::HackrfSweepSequenceGate linear_gate(std::move(linear));
    expect(linear_gate.expected_blocks().size() == 2U &&
           linear_gate.expected_blocks()[0].reported_tuned_frequency_hz == 7'000'000'000U &&
           linear_gate.expected_blocks()[1].reported_tuned_frequency_hz == 7'020'000'000U,
           "linear order and high raw frequency must not inherit RTBW envelope");
}

void pinned_firmware_recurrence_matches_generated_sequence() {
    for (const auto style : {sdr_hackrf::HackrfSweepStyle::Linear,
                             sdr_hackrf::HackrfSweepStyle::Interleaved}) {
        for (const std::uint32_t step_hz : {4'000'000U, 20'000'000U}) {
            sdr_hackrf::HackrfSweepSequencePlan plan{
                .ranges = {{100U, 140U}, {200U, 260U}, {7'000U, 7'040U}},
                .step_width_hz = step_hz,
                .style = style,
            };
            sdr_hackrf::HackrfSweepSequenceGate gate(plan);
            const auto expected = gate.expected_blocks();
            std::size_t range_index = 0U;
            std::uint64_t frequency =
                static_cast<std::uint64_t>(plan.ranges[0].start_mhz) * 1'000'000U;
            bool odd = true;
            for (std::size_t index = 0U; index < expected.size(); ++index) {
                expect(expected[index].reported_tuned_frequency_hz == frequency,
                       "precomputed sequence must match pinned firmware recurrence");
                const auto stop_hz =
                    static_cast<std::uint64_t>(plan.ranges[range_index].stop_mhz) * 1'000'000U;
                if (style == sdr_hackrf::HackrfSweepStyle::Interleaved) {
                    if (!odd && frequency + step_hz >= stop_hz) {
                        range_index = (range_index + 1U) % plan.ranges.size();
                        frequency = static_cast<std::uint64_t>(
                            plan.ranges[range_index].start_mhz) * 1'000'000U;
                    } else {
                        frequency += odd ? step_hz / 4U : 3U * step_hz / 4U;
                    }
                    odd = !odd;
                } else if (frequency + step_hz >= stop_hz) {
                    range_index = (range_index + 1U) % plan.ranges.size();
                    frequency = static_cast<std::uint64_t>(
                        plan.ranges[range_index].start_mhz) * 1'000'000U;
                } else {
                    frequency += step_hz;
                }
            }
            expect(range_index == 0U &&
                   frequency == static_cast<std::uint64_t>(
                       plan.ranges[0].start_mhz) * 1'000'000U,
                   "one complete expected cycle must wrap to its origin");
        }
    }
}

void transfer_limit_is_checked_before_any_header() {
    auto plan = example_plan();
    plan.max_transfer_blocks = 1U;
    sdr_hackrf::HackrfSweepSequenceGate gate(std::move(plan));
    const std::array<std::uint64_t, 2> pair{100'000'000U, 105'000'000U};
    auto bytes = transfer(pair);
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 2> decisions{};
    const auto rejected = admit(gate, bytes, decisions);
    expect(rejected.status == sdr_hackrf::HackrfSweepTransferStatus::AboveConfiguredLimit &&
           rejected.blocks_visited == 0U && gate.metrics().blocks_seen == 0U,
           "oversized callback must not advance the frequency cursor");
    const std::array<std::uint64_t, 1> start{100'000'000U};
    auto start_bytes = transfer(start);
    std::array<sdr_hackrf::HackrfSweepBlockDecision, 1> first{};
    static_cast<void>(admit(gate, start_bytes, first));
    expect(first[0].admitted() && first[0].gap_before,
           "an oversized callback must remain an explicit scan gap");
}

template <typename Change>
void invalid_plan(Change change, const std::string& description) {
    auto plan = example_plan();
    change(plan);
    bool rejected = false;
    try {
        sdr_hackrf::HackrfSweepSequenceGate gate(std::move(plan));
        static_cast<void>(gate);
    } catch (const std::exception&) {
        rejected = true;
    }
    expect(rejected, description);
}

void invalid_plans_fail_before_sdk() {
    invalid_plan([](auto& p) { p.ranges.clear(); }, "empty ranges");
    invalid_plan([](auto& p) { p.ranges[0] = {140U, 100U}; }, "reversed range");
    invalid_plan([](auto& p) { p.ranges[1] = {120U, 160U}; }, "overlapping ranges");
    invalid_plan([](auto& p) { p.ranges[0] = {100U, 130U}; }, "partial final step");
    invalid_plan([](auto& p) { p.step_width_hz = 0U; }, "zero step");
    invalid_plan([](auto& p) { p.step_width_hz = 20'000'002U; },
                 "fractional interleaved quarters");
    invalid_plan([](auto& p) { p.max_transfer_blocks = 0U; }, "zero transfer bound");
    invalid_plan([](auto& p) {
        p.max_transfer_blocks = sdr_hackrf::hackrf_sweep_max_transfer_blocks + 1U;
    }, "oversized transfer bound");
    invalid_plan([](auto& p) { p.style = static_cast<sdr_hackrf::HackrfSweepStyle>(255U); },
                 "unknown firmware style");
    invalid_plan([](auto& p) { p.ranges.assign(11U, {100U, 120U}); },
                 "more than pinned firmware range count");
    invalid_plan([](auto& p) {
        p.ranges = {{0U, 65'535U}};
        p.step_width_hz = 4U;
    }, "bounded precomputed sequence");
}

}  // namespace

int main() {
    try {
        pinned_interleaved_order_and_epochs();
        missing_corrupt_and_out_of_order_are_visible();
        first_frequency_sync_and_early_restart();
        transfer_and_downstream_failures_do_not_partially_admit();
        same_transfer_sink_drop_marks_the_next_block();
        repeated_origin_stays_in_existing_scan();
        out_of_plan_and_linear_mode();
        pinned_firmware_recurrence_matches_generated_sequence();
        transfer_limit_is_checked_before_any_header();
        invalid_plans_fail_before_sdk();
        std::cout << "HackRF firmware Sweep header-sequence admission PASS\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
