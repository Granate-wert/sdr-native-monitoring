#pragma once

#include <cstddef>
#include <cstdint>
#include <span>

namespace sdr_hackrf {

// Official libhackrf sweep mode prefixes each fixed-size block with
// 0x7f, 0x7f and an eight-byte little-endian tuned frequency. The remainder
// is interleaved CI8, not an ordinary fixed-centre RTBW callback. These
// constants match the qualified HackRF SDK's BYTES_PER_BLOCK contract; an
// eventual active sweep owner must check SDK/API compatibility before RX.
inline constexpr std::size_t hackrf_sweep_block_bytes = 16'384U;
inline constexpr std::size_t hackrf_sweep_header_bytes = 10U;
inline constexpr std::size_t hackrf_sweep_ci8_bytes =
    hackrf_sweep_block_bytes - hackrf_sweep_header_bytes;
static_assert(hackrf_sweep_ci8_bytes % 2U == 0U);

enum class HackrfSweepBlockStatus : std::uint8_t {
    SyntaxValid,
    InvalidSize,
    InvalidMarker,
};

struct HackrfSweepBlockView {
    // Reported by the firmware header. It is not yet an admitted plan segment,
    // calibrated frequency or proof that post-retune samples have settled.
    std::uint64_t reported_tuned_frequency_hz{};
    // Borrows the callback buffer; no consumer may retain this span after the
    // callback/owning lease expires. The bytes are signed CI8 pairs on decode.
    std::span<const std::uint8_t> interleaved_ci8{};
};

struct HackrfSweepBlockParseResult {
    HackrfSweepBlockStatus status{HackrfSweepBlockStatus::InvalidSize};
    HackrfSweepBlockView block{};

    [[nodiscard]] bool syntax_valid() const noexcept {
        return status == HackrfSweepBlockStatus::SyntaxValid;
    }
};

// Syntax-only, allocation-free parser for ONE aligned firmware sweep block.
// It never guesses a frequency for missing/corrupt headers. A decoded value
// is NOT an admissible device frequency or proof of a requested tune. Range,
// exact plan, order, discontinuity and settling validation belong to the
// future same-owner sweep coordinator before any spectrum publication.
[[nodiscard]] HackrfSweepBlockParseResult parse_hackrf_sweep_block(
    std::span<const std::uint8_t> bytes
) noexcept;

}  // namespace sdr_hackrf
