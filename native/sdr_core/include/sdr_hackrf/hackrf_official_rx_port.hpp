#pragma once

#include "sdr_hackrf/hackrf_rx_session.hpp"

#include <memory>
#include <array>
#include <cstdint>
#include <optional>

namespace sdr_hackrf {

// Implemented only by the optional target compiled against the official
// private libhackrf header/import library. The public surface stays SDK-free.
[[nodiscard]] std::unique_ptr<HackrfRxRuntimePort> make_official_hackrf_rx_port(
    std::optional<std::array<std::uint32_t, 4>> expected_serial_words = std::nullopt
);

}  // namespace sdr_hackrf
