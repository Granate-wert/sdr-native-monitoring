#include "sdr_hackrf/hackrf_sweep_block.hpp"

namespace sdr_hackrf {

HackrfSweepBlockParseResult parse_hackrf_sweep_block(
    const std::span<const std::uint8_t> bytes
) noexcept {
    if (bytes.size() != hackrf_sweep_block_bytes) {
        return {HackrfSweepBlockStatus::InvalidSize, {}};
    }
    if (bytes[0] != 0x7fU || bytes[1] != 0x7fU) {
        return {HackrfSweepBlockStatus::InvalidMarker, {}};
    }
    std::uint64_t frequency_hz = 0U;
    for (std::size_t index = 0U; index < 8U; ++index) {
        frequency_hz |= static_cast<std::uint64_t>(bytes[index + 2U]) << (8U * index);
    }
    return {
        HackrfSweepBlockStatus::SyntaxValid,
        {frequency_hz, bytes.subspan(hackrf_sweep_header_bytes)},
    };
}

}  // namespace sdr_hackrf
