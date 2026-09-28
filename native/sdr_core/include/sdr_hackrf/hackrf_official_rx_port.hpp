#pragma once

#include "sdr_hackrf/hackrf_rx_session.hpp"
#include "sdr_hackrf/hackrf_sweep_session.hpp"

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

// Same official single-device implementation, but an explicit Sweep-typed
// factory. The existing RTBW factory and its start_rx() path are unchanged.
[[nodiscard]] std::unique_ptr<HackrfSweepRuntimePort> make_official_hackrf_sweep_port(
    std::array<std::uint32_t, 4> expected_serial_words
);

}  // namespace sdr_hackrf
