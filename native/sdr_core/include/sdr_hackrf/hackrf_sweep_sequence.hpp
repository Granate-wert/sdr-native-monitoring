#pragma once

#include "sdr_hackrf/hackrf_sweep_block.hpp"

#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace sdr_hackrf {

inline constexpr std::size_t hackrf_sweep_max_ranges = 10U;
inline constexpr std::size_t hackrf_sweep_max_expected_blocks = 65'536U;
inline constexpr std::size_t hackrf_sweep_max_transfer_blocks = 1'024U;

struct HackrfSweepRange {
    std::uint16_t start_mhz{};
    std::uint16_t stop_mhz{};
};

enum class HackrfSweepStyle : std::uint8_t {
    Linear,
    Interleaved,
};

// Byte-order plan for the pinned firmware's one-block-per-tune Sweep mode.
// It is NOT an RF capability or usable-spectrum plan. The future same-device
// owner must separately admit device/API version, LO+offset, filter, usable
// subbands, transport and the requested Analyzer span before invoking the SDK.
struct HackrfSweepSequencePlan {
    std::vector<HackrfSweepRange> ranges;
    std::uint32_t step_width_hz{20'000'000U};
    std::uint32_t offset_hz{7'500'000U};
    HackrfSweepStyle style{HackrfSweepStyle::Interleaved};
    std::size_t max_transfer_blocks{16U};
};

struct HackrfSweepExpectedBlock {
    std::uint64_t reported_tuned_frequency_hz{};
    std::uint32_t range_index{};
    std::uint32_t step_index{};
    std::uint8_t interleave_phase{};
};

enum class HackrfSweepBlockDecisionStatus : std::uint8_t {
    Admitted,
    InvalidMarker,
    OutOfPlan,
    AwaitingFirstFrequency,
    OutOfOrder,
};

struct HackrfSweepBlockDecision {
    HackrfSweepBlockDecisionStatus status{HackrfSweepBlockDecisionStatus::InvalidMarker};
    // Nonempty only for Admitted. This is still borrowed callback data, not
    // a SpectrumFrame or a checked usable RF subband.
    std::span<const std::uint8_t> interleaved_ci8{};
    std::uint64_t reported_tuned_frequency_hz{};
    // Host-inferred logical epochs from header order; firmware has no unique
    // sweep counter or hardware timestamp. An early origin is explicitly
    // gapped but cannot be proved fresh from its frequency alone.
    std::uint64_t scan_epoch{};
    std::uint64_t continuity_epoch{};
    std::uint32_t plan_index{};
    std::uint32_t range_index{};
    std::uint32_t step_index{};
    std::uint8_t interleave_phase{};
    // A jump in the known header sequence. It may include a separately
    // observed malformed/rejected block; never add it to other loss counters
    // as an independent count of missing USB or RF samples.
    std::uint32_t known_skipped_headers_before{};
    bool gap_before{};
    bool new_scan{};

    [[nodiscard]] bool admitted() const noexcept {
        return status == HackrfSweepBlockDecisionStatus::Admitted;
    }
};

enum class HackrfSweepTransferStatus : std::uint8_t {
    Processed,
    MalformedLength,
    AboveConfiguredLimit,
    SinkMissing,
};

struct HackrfSweepTransferResult {
    HackrfSweepTransferStatus status{HackrfSweepTransferStatus::MalformedLength};
    std::size_t blocks_visited{};
};

// Called synchronously for each block while its callback buffer is alive.
// Return false only when an Admitted block could not cross the next bounded
// owner boundary. The gate then marks the NEXT block (even in this transfer)
// with a gap. The sink must not retain the borrowed CI8 span.
using HackrfSweepBlockSink = bool (*)(
    const HackrfSweepBlockDecision& decision,
    void* context
) noexcept;

struct HackrfSweepSequenceMetrics {
    std::uint64_t transfers_seen{};
    std::uint64_t transfers_rejected{};
    std::uint64_t blocks_seen{};
    std::uint64_t blocks_admitted{};
    std::uint64_t invalid_markers{};
    std::uint64_t out_of_plan{};
    std::uint64_t awaiting_first_frequency{};
    std::uint64_t out_of_order{};
    std::uint64_t downstream_drops{};
    std::uint64_t known_skipped_headers{};
    // Count of rejected input events, not an RF/USB sample-loss estimate.
    std::uint64_t unlocated_rejections{};
    std::uint64_t scan_epoch{};
    std::uint64_t continuity_epoch{};
    std::uint32_t next_plan_index{};
    bool synchronized{};
    bool gap_pending{};
};

// Precomputes the exact header sequence from the pinned firmware's LINEAR or
// INTERLEAVED progression. Callback-facing admission is allocation-free and
// noexcept. The owner handles each admitted block synchronously in the sink,
// before the next block is admitted; this gate itself owns no SDK/device,
// worker or output queue. Construction and callback admission are single-owner;
// cross-thread metrics snapshots require synchronization by that future owner.
class HackrfSweepSequenceGate final {
public:
    explicit HackrfSweepSequenceGate(HackrfSweepSequencePlan plan);

    [[nodiscard]] const HackrfSweepSequencePlan& plan() const noexcept;
    [[nodiscard]] std::span<const HackrfSweepExpectedBlock> expected_blocks() const noexcept;
    [[nodiscard]] HackrfSweepSequenceMetrics metrics() const noexcept;

    [[nodiscard]] HackrfSweepTransferResult admit_transfer(
        std::span<const std::uint8_t> callback_bytes,
        HackrfSweepBlockSink sink,
        void* context
    ) noexcept;

    // Callback-thread only, before the next block is admitted. A sink may
    // instead return false to call this automatically. Asynchronous worker
    // drops need separate downstream quality accounting by the future owner.
    void note_downstream_drop() noexcept;

private:
    [[nodiscard]] HackrfSweepBlockDecision admit_one(
        std::span<const std::uint8_t> bytes
    ) noexcept;
    void note_unlocated_rejection() noexcept;

    HackrfSweepSequencePlan plan_;
    std::vector<HackrfSweepExpectedBlock> expected_;
    HackrfSweepSequenceMetrics metrics_{};
    bool pending_gap_{};
};

}  // namespace sdr_hackrf
